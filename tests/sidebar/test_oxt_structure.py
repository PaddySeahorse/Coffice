"""Structural tests for the Coffice.oxt build.

LibreOffice is not required: these tests run ``extension/build.sh`` and verify
the produced .oxt is structurally correct (zip layout, XML well-formedness,
manifest consistency, and that the UNO component/service names declared in the
.xcu configuration match the Python component in ``python/coffice_panel.py``).

Manual in-LO verification steps (no LO in CI) are documented in the README and
in ``src/coffice/sidebar/__init__.py``.
"""

from __future__ import annotations

import py_compile
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXT_DIR = REPO_ROOT / "extension"
OXT_PATH = REPO_ROOT / "dist" / "Coffice.oxt"

EXPECTED_ENTRIES = {
    "description.xml",
    "description/description.txt",
    "description/title.txt",
    "META-INF/manifest.xml",
    "Sidebar.xcu",
    "Factories.xcu",
    "ProtocolHandler.xcu",
    "Addons.xcu",
    "icons/coffice.png",
    "python/coffice_panel.py",
    "python/coffice/__init__.py",
    "python/coffice/contract.py",
    "python/coffice/doc_commands.py",
}


@pytest.fixture(scope="module")
def oxt_path() -> Path:
    """Build the extension once and return the path to the .oxt."""
    result = subprocess.run(
        ["bash", str(EXT_DIR / "build.sh")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"build.sh failed:\n{result.stdout}\n{result.stderr}"
    assert OXT_PATH.is_file(), "build.sh did not produce dist/Coffice.oxt"
    return OXT_PATH


def read_zip(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as zf:
        return zf.read(name)


def test_build_produces_expected_entries(oxt_path: Path) -> None:
    with zipfile.ZipFile(oxt_path) as zf:
        names = set(zf.namelist())
    assert EXPECTED_ENTRIES <= names
    assert "META-INF/manifest.xml" in names


def test_manifest_declares_existing_files(oxt_path: Path) -> None:
    root = ET.fromstring(read_zip(oxt_path, "META-INF/manifest.xml"))
    ns = {"m": "http://openoffice.org/2001/manifest"}
    with zipfile.ZipFile(oxt_path) as zf:
        for entry in root.findall("m:file-entry", ns):
            full_path = entry.attrib.get(f"{{{ns['m']}}}full-path")
            assert full_path in zf.namelist(), f"manifest declares missing {full_path}"


def test_xcu_files_are_well_formed(oxt_path: Path) -> None:
    for name in ("Sidebar.xcu", "Factories.xcu", "ProtocolHandler.xcu", "Addons.xcu"):
        root = ET.fromstring(read_zip(oxt_path, name))
        assert root.tag.endswith("component-data")
        assert _root_attrs(root).get("package"), name
    ET.fromstring(read_zip(oxt_path, "description.xml"))


def _sidebar_xcu(oxt_path: Path) -> ET.Element:
    return ET.fromstring(read_zip(oxt_path, "Sidebar.xcu"))


def _factories_xcu(oxt_path: Path) -> ET.Element:
    return ET.fromstring(read_zip(oxt_path, "Factories.xcu"))


def _protocol_xcu(oxt_path: Path) -> ET.Element:
    return ET.fromstring(read_zip(oxt_path, "ProtocolHandler.xcu"))


def find_node(node: ET.Element, *names: str) -> ET.Element | None:
    """Navigate <node oor:name="..."> children by their (namespaced) name."""
    current = node
    for name in names:
        for child in current.findall("node"):
            attrs = {key.split("}")[-1]: value for key, value in child.attrib.items()}
            if attrs.get("name") == name:
                current = child
                break
        else:
            return None
    return current


def _prop(node: ET.Element | None, name: str) -> str:
    if node is None:
        return ""
    for prop in node.findall("prop"):
        attrs = {key.split("}")[-1]: value for key, value in prop.attrib.items()}
        if attrs.get("name") == name:
            value = prop.find("value")
            return (value.text or "").strip() if value is not None else ""
    return ""


def _root_attrs(root: ET.Element) -> dict[str, str]:
    return {key.split("}")[-1]: value for key, value in root.attrib.items()}


def test_sidebar_xcu_deck_and_panel_are_consistent(oxt_path: Path) -> None:
    root = _sidebar_xcu(oxt_path)
    deck = find_node(root, "Content", "DeckList", "CofficeDeck")
    panel = find_node(root, "Content", "PanelList", "CofficePanel")
    assert deck is not None and panel is not None
    assert _prop(deck, "Id") == "org.coffice.deck"
    assert _prop(panel, "DeckId") == _prop(deck, "Id")
    assert "Writer" in _prop(deck, "ContextList")
    assert "Writer" in _prop(panel, "ContextList")


def test_panel_implementation_url_matches_factory_name(oxt_path: Path) -> None:
    panel = find_node(_sidebar_xcu(oxt_path), "Content", "PanelList", "CofficePanel")
    factory = find_node(
        _factories_xcu(oxt_path), "Registered", "UIElementFactories", "CofficeFactory"
    )
    assert panel is not None and factory is not None

    implementation_url = _prop(panel, "ImplementationURL")
    assert implementation_url.startswith("private:resource/toolpanel/")
    factory_name = _prop(factory, "Name")
    assert factory_name, "Factories.xcu Name must be set"
    assert implementation_url == f"private:resource/toolpanel/{factory_name}/CofficePanel"
    assert _prop(factory, "Type") == "toolpanel"


def test_factory_implementation_matches_python_component(oxt_path: Path) -> None:
    factory = find_node(
        _factories_xcu(oxt_path), "Registered", "UIElementFactories", "CofficeFactory"
    )
    factory_impl = _prop(factory, "FactoryImplementation")
    panel = find_node(_sidebar_xcu(oxt_path), "Content", "PanelList", "CofficePanel")
    source = read_zip(oxt_path, "python/coffice_panel.py").decode("utf-8")
    assert factory_impl == "org.coffice.sidebar.PanelFactory"
    assert f'IMPL_FACTORY = "{factory_impl}"' in source
    assert f'RESOURCE_URL = "{_prop(panel, "ImplementationURL")}"' in source


def test_protocol_handler_registration_matches_python(oxt_path: Path) -> None:
    root = _protocol_xcu(oxt_path)
    handler = find_node(root, "HandlerSet", "org.coffice.sidebar.ProtocolHandler")
    assert handler is not None
    source = read_zip(oxt_path, "python/coffice_panel.py").decode("utf-8")
    assert 'IMPL_PROTOCOL_HANDLER = "org.coffice.sidebar.ProtocolHandler"' in source
    protocols = " ".join(v.text or "" for v in handler.findall(".//value"))
    assert "org.coffice:*" in protocols


def test_addons_menu_toggles_sidebar_via_protocol(oxt_path: Path) -> None:
    root = ET.fromstring(read_zip(oxt_path, "Addons.xcu"))
    menu_item = find_node(
        root, "AddonUI", "OfficeMenuBarMerging", "org.coffice.sidebar", "S1", "MenuItems", "M1"
    )
    assert menu_item is not None
    url = _prop(menu_item, "URL")
    assert url == "org.coffice:ToggleSidebar"
    title = _prop(menu_item, "Title")
    assert title.lower() == "coffice"
    source = read_zip(oxt_path, "python/coffice_panel.py").decode("utf-8")
    assert "ToggleSidebar" in source


def test_panel_default_menu_command_uses_protocol(oxt_path: Path) -> None:
    panel = find_node(_sidebar_xcu(oxt_path), "Content", "PanelList", "CofficePanel")
    assert _prop(panel, "DefaultMenuCommand") == "org.coffice:ToggleSidebar"


def test_python_files_compile(oxt_path: Path, tmp_path: Path) -> None:
    with zipfile.ZipFile(oxt_path) as zf:
        for name in zf.namelist():
            if name.endswith(".py"):
                target = tmp_path / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
                py_compile.compile(str(target), doraise=True)


def test_shipped_contract_matches_source(oxt_path: Path) -> None:
    shipped = read_zip(oxt_path, "python/coffice/contract.py").decode("utf-8")
    shipped_body = "\n".join(shipped.splitlines()[1:]).rstrip("\n")
    source_path = REPO_ROOT / "src" / "coffice" / "sidebar" / "contract.py"
    source = source_path.read_text(encoding="utf-8").rstrip("\n")
    assert shipped_body == source


def test_shipped_doc_commands_matches_source(oxt_path: Path) -> None:
    shipped = read_zip(oxt_path, "python/coffice/doc_commands.py").decode("utf-8")
    shipped_body = "\n".join(shipped.splitlines()[1:]).rstrip("\n")
    source_path = REPO_ROOT / "src" / "coffice" / "sidebar" / "doc_commands.py"
    source = source_path.read_text(encoding="utf-8").rstrip("\n")
    assert shipped_body == source


def test_icon_is_valid_png(oxt_path: Path) -> None:
    data = read_zip(oxt_path, "icons/coffice.png")
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
