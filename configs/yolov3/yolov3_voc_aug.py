from pathlib import Path


__data = 'voc'
__input_size = 416
__version = 'yolov3_voc_aug'

if Path('/content/object_detection').exists():
    __data_dir = '/content/object_detection/data/' + __data
    __out_dir = '/content/drive/MyDrive/result/' + __version
else:
    __data_dir = '/home/sato/work/object_detection/data/' + __data
    __out_dir = '/home/sato/work/object_detection/result/' + __version

# データ
__mean = [0.485, 0.456, 0.406]
__std = [0.229, 0.224, 0.225]
data = dict(
    data_dir=__data_dir,
    bbox_fmt='xywh',
    train_pipeline=dict(
        albu=[
            dict(type='ShiftScaleRotate', rotate_limit=15, scale_limit=(-0.25, 0.0)),
            dict(type='PadIfNeeded', min_height=300, min_width=300),
            dict(type='RandomSizedBBoxSafeCrop', height=__input_size, width=__input_size, erosion_rate=0.4),
            dict(type='RGBShift'),
            dict(type='ColorJitter', brightness=0.125, contrast=0.5, saturation=0.5, hue=0.05),
        ],
        torch=[
            dict(type='ToTensor'),
            dict(type='GridErasing', min_stride_ratio=0.1, max_stride_ratio=0.2),
            dict(type='Normalize', mean=__mean, std=__std)
        ]
    ),
    val_pipeline=dict(
        albu=[
            dict(type='Resize', height=__input_size, width=__input_size)
        ],
        torch=[
            dict(type='ToTensor'),
            dict(type='Normalize', mean=__mean, std=__std)
        ]
    )
)

# モデル
model = dict(type='yolov3', num_classes=20, backborn='Darknet53', backborn_weight=None)

# 学習
train_conditions = [
    dict(keys=['.'])
]
optimizer = dict(type='SGD', lr=0.0026, momentum=0.9, weight_decay=0.0005)
scheduler = dict(type='MultiStepLRWarmUpRestarts', milestones=[50, 75], gamma=0.1, eta_min=0.0001, T_up=10)
runtime = dict(
    batch_size=2,
    epochs=100,
    out_dir=__out_dir,
    resume=True,
    eval_interval=10
)

# 予測・評価
predictor = dict(
    conf_thresh=0.4,
    iou_thresh=0.45
)
evaluator = dict(
    anno_path=__data_dir + '/annotations/instances_val.json',
    pred_path=__out_dir + '/test/pred_val.json'
)