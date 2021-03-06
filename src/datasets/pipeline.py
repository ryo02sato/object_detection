import torch
import torch.nn as nn
import albumentations as A
import torchvision.transforms as T
from datasets import aug_albu as AA  # noqa
from random import random, randint


class Compose:
    def __init__(self, transforms):
        self.transforms = transforms

    def __call__(self, image, image_meta):
        for t in self.transforms:
            image = t(image)
            if isinstance(t, T.ToTensor):
                image /= 255.  # ToTensor のinput が np.float32 の場合は正規化されないため
            elif isinstance(t, T.Normalize):
                image_meta['norm_mean'] = t.mean
                image_meta['norm_std'] = t.std

        return image, image_meta


class GridErasing(nn.Module):
    def __init__(self, p=0.5, min_stride_ratio=0.1, max_stride_ratio=0.2):
        super(GridErasing, self).__init__()
        self.p = p
        self.min_stride_ratio = min_stride_ratio
        self.max_stride_ratio = max_stride_ratio

    def forward(self, x: torch.Tensor):
        # get params
        stride = int(x.size(-1) * ((self.max_stride_ratio - self.min_stride_ratio) * random() + self.min_stride_ratio))
        grid_size = randint(int(stride * 0.3), int(stride * 0.7))

        grid_hws = torch.cartesian_prod(
            torch.arange(randint(0, stride), x.size(1), stride),
            torch.arange(randint(0, stride), x.size(2), stride)
        )

        for h, w in grid_hws:
            if torch.rand(1) < self.p:
                erase = torch.empty_like(x[..., h:h+grid_size, w:w+grid_size], dtype=torch.float32).normal_()
                x[..., h:h+grid_size, w:w+grid_size] = erase
        return x


class Dropout(nn.Module):
    """ 画素値を Dropout する
    1) 確率でランダムなピクセルを 0 にする
    2) 0 に近い値を 0 にする（ Pad の fill_value が 0 になる想定）
    """

    def __init__(self, p=(0, 0.05)):
        super(Dropout, self).__init__()
        if isinstance(p, tuple):
            self.p = (p[1] - p[0]) * random() + p[0]
        else:
            self.p = p

    def forward(self, x: torch.Tensor):
        _, h, w = x.size()
        x = x * (torch.rand(1, h, w) > self.p)
        return x


class Pipeline:
    """ データ変換（データ拡張）

    Args:
        pipeline: Augmentation の定義辞書

    Example:
        >>> transform = Pipeline(pipeline)
        >>> image, image_meta, bboxes, labels = transform(image, image_meta, bboxes, labels)
    """

    def __init__(self, pipeline: dict):
        self.albu_pipeline = self._build_albu(pipeline['albu'])
        self.torch_pipeline = self._build_torch(pipeline['torch'])

    def __call__(self, image, image_meta, bboxes, labels):
        # albumentations process
        image, image_meta, bboxes, labels = self.albu_pipeline(
            image=image,
            image_meta=image_meta,
            bboxes=bboxes,
            labels=labels
        ).values()

        # torchvision process
        image, image_meta = self.torch_pipeline(image, image_meta)

        return image, image_meta, bboxes, labels

    def _build_albu(self, pipe_cfg):
        transforms = []
        for cfg in pipe_cfg:
            type = cfg.pop('type')
            try:
                transforms.append(eval('A.' + type)(**cfg))
            except AttributeError:
                transforms.append(eval('AA.' + type)(**cfg))
        return A.Compose(
            transforms,
            bbox_params=A.BboxParams(format='coco', label_fields=['labels'])
        )

    def _build_torch(self, pipe_cfg):
        transforms = []
        for cfg in pipe_cfg:
            type = cfg.pop('type')
            try:
                transforms.append(eval('T.' + type)(**cfg))
            except AttributeError:
                transforms.append(eval(type)(**cfg))
        return Compose(transforms)
