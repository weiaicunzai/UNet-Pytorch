import os
import argparse
import torch
import torch.nn as nn
import torch.optim as optim
import cv2
import sys

import transforms
from model import UNet
from torch.utils.data.sampler import SubsetRandomSampler
from conf import settings

from lr_scheduler import WarmUpLR
from dataset import TableBorder
from utils import Metrics



if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('-b', type=int, default=12,
                        help='batch size for dataloader')
    parser.add_argument('-lr', type=float, default=0.1,
                        help='initial learning rate')
    parser.add_argument('-e', type=int, default=100, help='training epoches')
    parser.add_argument('-warm', type=int, default=5, help='warm up phase')
    parser.add_argument('-c', type=int, default=2, help='number of class')

    args = parser.parse_args()

    checkpoint_path = os.path.join(settings.CHECKPOINT_PATH, settings.TIME_NOW)

    if not os.path.exists(checkpoint_path):
        os.makedirs(checkpoint_path)
    checkpoint_path = os.path.join(checkpoint_path, '{epoch}-{type}.pth')

    train_transforms = transforms.Compose([
        transforms.RandomResizedCrop(settings.IMAGE_SIZE),
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(brightness=0.4, saturation=0.4, hue=0.4),
        transforms.ToTensor(),
        transforms.Normalize(settings.MEAN, settings.STD)
    ])

    valid_transforms = transforms.Compose([
        transforms.ToTensor()
    ])

    train_dataset = TableBorder(
        settings.DATA_PATH, transforms=train_transforms)
    valid_dataset = TableBorder(
        settings.DATA_PATH, transforms=train_transforms)

    split = int(0.2 * len(train_dataset))

    indices = list(range(len(valid_dataset)))
    train_indices, val_indices = indices[split:], indices[:split]
    print(val_indices)

    train_sampler = SubsetRandomSampler(train_indices)
    valid_sampler = SubsetRandomSampler(val_indices)

    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=args.b,
                                               sampler=train_sampler)
    validation_loader = torch.utils.data.DataLoader(valid_dataset, batch_size=args.b,
                                                    sampler=valid_sampler)

    net = UNet(3, args.c)
    net = net.cuda()
    optimizer = optim.SGD(net.parameters(), lr=args.lr,
                          momentum=0.9, weight_decay=1e-4, nesterov=True)
    iter_per_epoch = len(train_indices) / args.b
    warmup_scheduler = WarmUpLR(optimizer, iter_per_epoch * args.warm)
    train_scheduler = optim.lr_scheduler.MultiStepLR(
        optimizer, milestones=settings.MILESTONES)
    loss_fn = nn.BCELoss()
    #loss_fn = nn.MSELoss()


    best_iou = 0 
    for epoch in range(1, args.e + 1):
        if epoch > args.warm:
            train_scheduler.step(epoch)

        net.train()

        ious = 0
        for batch_idx, (images, masks) in enumerate(train_loader):
            if epoch <= args.warm:
                warmup_scheduler.step()

            images = images.cuda()
            masks = masks.cuda()

            optimizer.zero_grad()

            preds = net(images)

            metric = Metrics(preds.clone(), masks.clone())
            recall = metric.recall()
            recall = torch.stack(recall, dim=0)
            recall = torch.mean(recall)

            precision = metric.precision()
            precision = torch.stack(precision, dim=0)
            precision = torch.mean(precision)

            gt_pos_mask = masks == 1
            gt_neg_mask = masks == 0

            preds_pos = preds[gt_pos_mask]
            preds_neg = preds[gt_neg_mask]

            gt_pos = masks[gt_pos_mask]
            gt_neg = masks[gt_neg_mask]

            loss_pos = loss_fn(preds_pos, gt_pos)
            loss_neg = loss_fn(preds_neg, gt_neg)
            loss = 0.4 * loss_neg + loss_pos
            loss.backward()

            optimizer.step()

            n_iter = (epoch - 1) * iter_per_epoch + batch_idx + 1
            print('Training Epoch: {epoch} [{trained_samples}/{total_samples} \
                    Lr:{lr:0.6f} Loss:{:0.4f} mIOU{miou:0.4f} F1:{f1:0.4f} \
                    Recall:{recall:0.4f} Precision:{precision:0.4f}'.format(
                loss.item(),
                epoch=epoch,
                trained_samples=batch_idx * args.b + len(images),
                total_samples=len(train_indices),
                miou=metric.mIOU().item(),
                f1=metric.F1().item(),
                recall=recall,
                precision=precision,
                lr=optimizer.param_groups[0]['lr']
            ))

        net.eval()
        test_loss = 0.0

        for batch_idx, (images, masks) in enumerate(validation_loader):

            images = images.cuda()
            masks = masks.cuda()

            preds = net(images)

            loss = loss_fn(preds, masks)
            test_loss += loss.item()

            metric = Metrics(preds, masks)
            n_iter = (epoch - 1) * iter_per_epoch + batch_idx + 1
            iou = metric.iou()
            iou = torch.stack(iou, dim=0)
            iou = torch.mean(iou)
            ious += (images.size(0) / float(split)) * iou

        print('Test set: Average loss: {:.4f}, mIOU: {:.4f}'.format(
            test_loss / split,
            ious  
        ))

        if best_iou < ious / split and epoch > settings.MILESTONES[1]:
            best_iou = ious / split
            torch.save(net.state_dict(),
                            checkpoint_path.format(epoch=epoch, type='best'))






