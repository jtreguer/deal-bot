import pytest
from conftest import make_spec


@pytest.mark.parametrize(
    ("text", "key", "warns"),
    [
        ("RTX 2000 Ada", "rtx-2000-ada", False),
        ("NVIDIA RTX 2000 Ada Generation", "rtx-2000-ada", False),
        ("RTX2000 ada", "rtx-2000-ada", False),
        ("2000 ADA 8GB", "rtx-2000-ada", False),
        ("Quadro 2000 ADA", "rtx-2000-ada", False),
        ("3500ada", "rtx-3500-ada", False),
        ("RTX 3500", "rtx-3500-ada", False),
        ("RTX 5000 Ada 16GB", "rtx-5000-ada", False),
        ("RTX-A1000-6GB", "rtx-a1000", False),
        ("RTX 1000 Ada", "rtx-1000-ada", False),
        ("RTX A2000 Ada", "rtx-2000-ada", True),
        ("A2000Ada", "rtx-2000-ada", True),
        ("NVIDA RTX 2000", "rtx-2000-ada", True),
        ("RTX 4090 16GB", "rtx-4090", False),
        ("Intel Iris Xe Graphics", "integrated", False),
        ("Intel Arc", "integrated", False),
        ("some mystery card", None, False),
    ],
)
def test_normalize_gpu(kb, text, key, warns):
    got, warn = kb.normalize_gpu(text)
    assert got == key
    assert bool(warn) == warns


@pytest.mark.parametrize(
    ("text", "key", "cls"),
    [
        ("i7-13800H", "i7-13800h", "i7"),
        ("I9-13900H", "i9-13900h", "i9"),
        ("Intel Core i7 13700H", "i7-13700h", "i7"),
        ("U9 185H", "core-ultra-9-185h", "ultra-9"),
        ("Ultra7 165H", "core-ultra-7-165h", "ultra-7"),
        ("Core Ultra 7 155H", "core-ultra-7-155h", "ultra-7"),
        ("i7 13e génération", None, "i7"),
        ("Ultra 7", None, "ultra-7"),
        (None, None, None),
    ],
)
def test_normalize_cpu(kb, text, key, cls):
    assert kb.normalize_cpu(text) == (key, cls)


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("DELL précision 5680 I9-13900H 32GB 2TB", "precision-5680"),
        ("Dell Precision 16 - 5690 Workstation Laptop", "precision-5690"),
        ('Dell 5690 16" Intel Ultra 7 165H Touch OLED', "precision-5690"),
        ('Dell Precision 5690 JGYGG 16" FHD', "precision-5690"),
        ("Dell Inspiron 5680 desktop i7-8700", None),
        ("Xeon X5680 server board", None),
        ("Dell Precision 5570 i7 32GB", None),
    ],
)
def test_match_model(kb, title, key):
    assert kb.match_model(title) == key


def test_validate_flags_gpu_never_offered(kb):
    v = kb.validate(make_spec(gpu="RTX 4090 16GB"), "Dell Precision 5680 RTX 4090")
    assert v.gpu_key == "rtx-4090"
    assert any("never offered" in x for x in v.invalid)


def test_validate_flags_a1000_on_5690(kb):
    v = kb.validate(make_spec(model="Precision 5690", cpu="Ultra 7 165H", gpu="RTX A1000"), "")
    assert v.model_key == "precision-5690"
    assert any("A1000" in x for x in v.invalid)


def test_validate_clean_listing(kb):
    v = kb.validate(make_spec(), "Dell Precision 5680")
    assert (v.model_key, v.cpu_key, v.gpu_key) == ("precision-5680", "i7-13800h", "rtx-2000-ada")
    assert v.invalid == v.warnings == []
