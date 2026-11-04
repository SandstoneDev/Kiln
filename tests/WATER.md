# Water bake regression (host only; no game data)

The accompanying port supports existing WSRF v1 grids and new WSRF v2 grids with masked vertex heights. Rebuild `psp_sound_conv` after applying these source changes, then use Kiln Update assets to rebuild the sound output. Merely replacing Python files will not update an already built converter.

From the Kiln root, with GLM installed:

```sh
c++ -std=c++20 -O2 -Ibakers/include -Ibakers/tools/psp/converter tests/test_water_bake.cpp bakers/src/pipeline/adt_alpha.cpp -o /tmp/kiln-water-test
/tmp/kiln-water-test
python -m unittest discover -s tests
```

The synthetic ADT test covers partial liquid rectangles, world-axis order, masks, sloped/negative/zero heights, invisible upper layers and invalid data. No client assets are needed. WMO liquids and liquid material classification are outside this change.

Set `KILN_SOUND_CONVERTER` to the built executable before running unittest to include the two-map end-to-end conversion/verification test. The normal sounds step now uses `--all-maps` so terrain water is exported outside map 0 too.
