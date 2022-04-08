# AUTOGENERATED! DO NOT EDIT! File to edit: 00_core.ipynb (unless otherwise specified).

__all__ = [
    "return_base_path_deduplicated",
    "check_uniques",
    "drop_duplicates",
    "get_id",
    "split_w_stratify",
    "train_valid_split_w_stratify",
    "prepare_dataset",
    "prepare_transforms",
    "flysotData",
    "prep_data",
    "collate_fn",
    "train_model",
]

# Cell
import transformers
from sklearn.metrics import confusion_matrix, ConfusionMatrixDisplay
from dataclasses import asdict
from collections import OrderedDict
from typing import Union, Tuple, Sequence, Set
from numpy.random import RandomState
import numpy as np
from datasets import Dataset
from datasets import load_dataset
from pathlib import Path
from sklearn.model_selection import StratifiedShuffleSplit
from torchvision.transforms import (
    CenterCrop,
    RandomErasing,
    RandomAutocontrast,
    Compose,
    Normalize,
    RandomHorizontalFlip,
    RandomResizedCrop,
    Resize,
    RandomAdjustSharpness,
    ToTensor,
)
import torch
from transformers import AutoFeatureExtractor, TrainingArguments, Trainer
from transformers import AutoModelForImageClassification
from datasets import load_metric
from rich import print
import re
from dataclasses import dataclass
from typing import Dict
import datasets

# Cell
def return_base_path_deduplicated(x):
    f = x["fpath"]
    f = re.sub(r"(\(\d\))", "", f)
    f = f.split(".")[0]
    f = f.rstrip()
    return {"clean_path": re.sub(r"(\(\d\))", "", f)}


# Cell
def check_uniques(example, uniques, column="clean_path"):
    if example[column] in uniques:
        uniques.remove(example[column])
        return True
    else:
        return False


# Cell
def drop_duplicates(ds):
    ds = ds.map(return_base_path_deduplicated)
    uniques = set(ds["clean_path"])
    ds = ds.filter(check_uniques, fn_kwargs={"uniques": uniques})
    return ds


# Cell
def get_id(example):
    x = example["fpath"]
    x = Path(x).name.split("_")
    return {"id": "_".join(x[:2] if len(x) >= 3 else x[:3])}


# Cell
def split_w_stratify(
    ds,
    test_size: Union[int, float],
    train_size: Union[int, float, None] = None,
    random_state: Union[int, RandomState, None] = None,
) -> Tuple[Dataset, Dataset]:
    labels = ds["label"]
    label_array = np.array(labels)
    train_inds, valid_inds = next(
        StratifiedShuffleSplit(
            n_splits=2, test_size=test_size, random_state=random_state
        ).split(np.zeros(len(labels)), y=label_array)
    )
    return ds.select(train_inds), ds.select(valid_inds)


# Cell
def train_valid_split_w_stratify(
    ds,
    valid_size: Union[int, float] = None,
    test_size: Union[int, float] = 0.3,
    train_size: Union[int, float, None] = None,
    random_state: Union[int, RandomState, None] = None,
) -> Tuple[Dataset, Dataset, Dataset]:
    train, valid_test = split_w_stratify(ds, test_size=test_size)
    valid, test = split_w_stratify(valid_test, test_size=test_size)
    return train, valid, test


# Cell
def prepare_dataset(ds):
    print("Preparing dataset...")
    print("dropping duplicates...")
    ds = drop_duplicates(ds)
    print("getting ID...")
    ds = ds.map(get_id)
    print("creating train, valid, test splits...")
    train, valid, test = train_valid_split_w_stratify(ds)
    data = {"train": train, "valid": valid, "test": test}
    for k, v in data.items():
        print(f"{k} has {len(v)} examples")
    return train, valid, test


# Cell
def prepare_transforms(model_checkpoint, train_ds, valid_ds, test_ds=None):
    feature_extractor = AutoFeatureExtractor.from_pretrained(model_checkpoint)
    normalize = Normalize(
        mean=feature_extractor.image_mean, std=feature_extractor.image_std
    )
    _train_transforms = Compose(
        [
            Resize((feature_extractor.size, feature_extractor.size)),
            RandomAdjustSharpness(0.1),
            RandomAutocontrast(),
            ToTensor(),
            normalize,
            RandomErasing(),
        ]
    )

    _val_transforms = Compose(
        [
            Resize((feature_extractor.size, feature_extractor.size)),
            ToTensor(),
            normalize,
        ]
    )

    def train_transforms(examples):
        examples["pixel_values"] = [
            _train_transforms(image.convert("RGB")) for image in examples["image"]
        ]
        return examples

    def val_transforms(examples):
        examples["pixel_values"] = [
            _val_transforms(image.convert("RGB")) for image in examples["image"]
        ]
        return examples

    if test_ds is not None:
        test_ds.set_transform(val_transforms)
    train_ds.set_transform(train_transforms)
    valid_ds.set_transform(val_transforms)
    return train_ds, valid_ds, test_ds


# Cell
@dataclass
class flysotData:
    train_ds: datasets.arrow_dataset.Dataset
    valid_ds: datasets.arrow_dataset.Dataset
    test_ds: datasets.arrow_dataset.Dataset
    id2label: Dict[int, str]
    label2id: Dict[str, int]


# Cell
def prep_data(ds_checkpoint="davanstrien/flysheet", model_checkpoint=None):
    try:
        ds = load_dataset(
            ds_checkpoint, use_auth_token=True, streaming=False, split="train"
        )
        labels = ds.info.features["label"].names
        id2label = dict(enumerate(labels))
        label2id = {v: k for k, v in id2label.items()}
        train, valid, test = prepare_dataset(ds)
        train_ds, valid_ds, test_ds = prepare_transforms(
            model_checkpoint, train, valid, test
        )
        return flysotData(train_ds, valid_ds, test_ds, id2label, label2id)
    except FileNotFoundError as e:
        print(f"{e} make sure you are logged into the Hugging Face Hub")


# Cell
def collate_fn(examples):
    pixel_values = torch.stack([example["pixel_values"] for example in examples])
    labels = torch.tensor([example["label"] for example in examples])
    return {"pixel_values": pixel_values, "labels": labels}


# Cell
def train_model(
    data,
    model_checkpoint,
    num_epochs=50,
    save_dir="flyswot_model",
    hub_model_id="flyswot",
    tune=False,
    fp16=True,
):
    transformers.logging.set_verbosity_warning()
    train_ds, valid_ds, test_ds, id2label, label2id = asdict(data).items()
    model = AutoModelForImageClassification.from_pretrained(
        model_checkpoint,
        num_labels=len(id2label),
        id2label=id2label,
        label2id=label2id,
        ignore_mismatched_sizes=True,
    )
    feature_extractor = AutoFeatureExtractor.from_pretrained(model_checkpoint)
    args = TrainingArguments(
        "output_dir",
        save_strategy="epoch",
        evaluation_strategy="epoch",
        hub_model_id=f"flyswot/{hub_model_id}",
        overwrite_output_dir=True,
        push_to_hub=True,
        learning_rate=2e-5,
        per_device_train_batch_size=4,
        per_device_eval_batch_size=4,
        num_train_epochs=num_epochs,
        weight_decay=0.1,
        disable_tqdm=False,
        fp16=fp16,
        load_best_model_at_end=True,
        metric_for_best_model="f1",
        logging_dir="logs",
        remove_unused_columns=False,
        save_total_limit=10,
        optim="adamw_torch",
        seed=42,
    )
    f1 = load_metric("f1")

    def compute_metrics(eval_pred):
        predictions, labels = eval_pred
        predictions = np.argmax(predictions, axis=1)
        return f1.compute(predictions=predictions, references=labels, average="macro")

    trainer = Trainer(
        model,
        args,
        train_dataset=train_ds,
        eval_dataset=valid_ds,
        data_collator=collate_fn,
        compute_metrics=compute_metrics,
        tokenizer=feature_extractor,
    )
    trainer.train()
    return trainer
