# PSP button glyphs

From Kenney's "Input Prompts" 1.5 (www.kenney.nl), **CC0 1.0** (public domain, see `License.txt`): the
PlayStation Series set (face buttons, D-pad, SELECT/START, stick) and the Nintendo Switch `L` / `R`
shoulder buttons (the PSP's shoulders have no "1"). `tools/psp/build_ui_pak.py` bakes them into
`ui.pak` as `pad_*`; the game draws them through `UiKit::glyph` (action bar hotkeys, prompts, tutorials).
