import argparse
import torch
import json
from shutil import rmtree
from datasets import DetectionDataset, MetaData
from torch.utils.data import DataLoader
from torch.optim import SGD
from torch.optim.lr_scheduler import MultiStepLR
from tensorboard.backend.event_processing import event_accumulator
from torch.utils.tensorboard import SummaryWriter
from models import SSD
from pathlib import Path
from torchsummary import summary
from tqdm import tqdm
from collections import defaultdict
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval


def chain(loaders: dict) -> tuple:
    """ dataloader を繋ぐ (train phase ~> val phase を連鎖的に行う)
    Args:
        loaders (dict): {
            'train': train dataloader
            'val': val dataloader
        }
    Yields:
        Iterator[tuple]: (phase, (images tensor, labels tensor))
    """
    for phase in ['train', 'val']:
        for element in loaders[phase]:
            yield phase, element


# ----------------- パラメータ設定 -----------------
parser = argparse.ArgumentParser()

parser.add_argument('--data_name', help='same as the directory name placed under ./data', default='voc')
parser.add_argument('--out_dir', help='directory to save weight files etc', default='./result')
parser.add_argument('--batch_size', help='batch size of loaded data', type=int, default=32)
parser.add_argument('--input_size', help='input image size to model', type=int, default=300)
parser.add_argument('--epochs', help='number of epochs', type=int, default=50)
parser.add_argument('--version', help='used for output directory name', default='ssd_voc')
parser.add_argument('--resume', help='set when resuming interrupted learning', action='store_true')

args = parser.parse_args()
# --------------------------------------------------

data_dir = f'./data/{args.data_name}'
meta = MetaData(data_dir=data_dir)

# 実行準備
log_dir = Path(args.out_dir) / args.version / 'logs'
weights_dir = Path(args.out_dir) / args.version / 'weights'
interim_dir = Path(args.out_dir) / args.version / 'interim'
initial_epoch = 1
if args.resume:
    for log_path in log_dir.glob('**/events.out.*'):
        ea = event_accumulator.EventAccumulator(log_path.as_posix())
        ea.Reload()
        if 'loss/train' in ea.Tags()['scalars']:
            initial_epoch = max(event.step for event in ea.Scalars('loss/train')) + 1
else:
    for d in [log_dir, weights_dir, interim_dir]:
        rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)

# データ生成
dataloaders = {}
for phase in ['train', 'val']:
    dataset = DetectionDataset(
        data_dir=data_dir,
        input_size=args.input_size,
        norm_cfg=meta.norm_cfg,
        phase=phase
    )

    dataloaders[phase] = DataLoader(
        dataset=dataset,
        batch_size=args.batch_size,
        collate_fn=dataset.collate_fn,
        shuffle=phase == 'train'
    )

# モデル
model = SSD(num_classes=meta.num_classes)

weights_path = weights_dir / 'latest.pth'
if weights_path.exists():
    model.load_state_dict(torch.load(weights_path, map_location=torch.device('cpu')))

# 学習
device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
model.to(device)

criterion = model.loss
optimizer = SGD(params=model.get_parameters(), lr=0.001, momentum=0.9, weight_decay=0.0005)
scheduler = MultiStepLR(optimizer, milestones=[int(args.epochs * 0.5), int(args.epochs * 0.75)])

# 推論
detector = model.detect
eval_interval = 10

torch.backends.cudnn.benchmark = True

print(f'''<-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><->
<-><-><-><-><-><-><-> TRAINING START ! <-><-><-><-><-><-><->
<-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><->
[CONFIG]
- version    : {args.version}
- batch_size : {args.batch_size}
- epochs     : {args.epochs}
- out_dir    : {args.out_dir}

[RUNTIME]
- {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}

[DATASET]
- {args.data_name}

[MODEL]
- {model.__class__.__name__}{args.input_size}

[MODEL SUMMARY]
{str(summary(model, (3, args.input_size, args.input_size), verbose=0))}

[OPTIMIZER]
- {optimizer.__class__.__name__}
- hyper params : {optimizer.defaults}

[SCHEDULER]
- {scheduler.__class__.__name__}
- milestones: lr = {scheduler.get_last_lr()[-1]:.1e} -> {
    ' -> '.join(
        f'{scheduler.get_last_lr()[-1] * pow(scheduler.gamma, i):.1e} ({s} epc~)'
        for i, s in enumerate(scheduler.milestones.keys(), start=1))}

<-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><-><->
''')
min_val_loss = 99999
with SummaryWriter(log_dir=log_dir) as writer:
    for epoch in range(initial_epoch, args.epochs + initial_epoch):
        losses = {'train': defaultdict(lambda: 0), 'val': defaultdict(lambda: 0)}
        counts = {'train': 0, 'val': 0}
        for phase, (images, image_metas, gt_bboxes, gt_labels) in tqdm(
                chain(dataloaders),
                total=sum(len(dl) for dl in dataloaders.values()),
                desc=f'[Epoch {epoch:3}]'):

            if phase == 'train':
                model.train()
                optimizer.zero_grad()
                torch.set_grad_enabled(True)
            else:
                model.eval()
                torch.set_grad_enabled(False)

            # to GPU device
            images = images.to(device)

            # forward + backward + optimize
            outputs = model(images)
            loss = criterion(outputs, gt_bboxes, gt_labels)

            if phase == 'train':
                loss['loss'].backward()
                optimizer.step()
            elif epoch % eval_interval == 0:
                detector(outputs, image_metas, interim_dir)

            for kind in loss.keys():
                losses[phase][kind] += loss[kind].item() * images.size(0)
            counts[phase] += images.size(0)

        for phase in ['train', 'val']:
            for kind in losses[phase].keys():
                losses[phase][kind] /= counts[phase]

        # 評価
        if epoch % eval_interval == 0:
            result = []
            for path in interim_dir.glob('**/*.json'):
                with open(path, 'r') as f:
                    res = json.load(f)
                result += res
            if len(result) > 0:
                with open(interim_dir / 'instances_val.json', 'w') as f:
                    json.dump(result, f)

                cocoGt = COCO((Path(data_dir) / 'annotations' / 'instances_val.json').as_posix())
                cocoDt = cocoGt.loadRes(interim_dir / 'instances_val.json')
                cocoEval = COCOeval(cocoGt, cocoDt, 'bbox')
                cocoEval.evaluate()
                cocoEval.accumulate()
                cocoEval.summarize()
            else:
                print('No Object Detected. Skip Evaluation')

        # tensor board への書き込み
        for phase in ['train', 'val']:
            for kind in losses[phase].keys():
                writer.add_scalar(f'{kind}/{phase}', losses[phase][kind], epoch)
        for i, lr in enumerate(scheduler.get_last_lr(), start=1):
            writer.add_scalar(f'lr/lr_{i}', lr, epoch)

        print(f'  loss     : {losses["train"].pop("loss"):.04f} ({", ".join([f"{kind}: {value:.04f}" for kind, value in losses["train"].items()])})')
        print(f'  val_loss : {losses["val"].pop("loss"):.04f} ({", ".join([f"{kind}: {value:.04f}" for kind, value in losses["val"].items()])})')

        # 重みファイル保存
        if losses['val']['loss'] < min_val_loss:
            torch.save(model.state_dict(), weights_dir / 'latest.pth')
            min_val_loss = losses['val']['loss']

        # スケジューラ更新
        scheduler.step()
