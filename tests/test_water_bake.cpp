#include "sound_bake.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <cstdio>
#include <limits>
using namespace wowee;
int main() {
    pipeline::ADTTerrain t;
    pipeline::ADTTerrain::WaterLayer l{};
    l.liquidType=7;l.x=3;l.y=2;l.width=1;l.height=1;l.minHeight=-5;l.maxHeight=50;
    l.heights={-5,-3,-4,-2};l.mask.resize(8);l.mask[2]=1u<<3;
    t.waterData[17].layers.push_back(l);
    // A declared but invisible upper layer must not raise swimming height.
    l.minHeight=100;l.maxHeight=100;l.mask[2]=0;
    t.waterData[17].layers.push_back(l);
    uint32_t hist[8]={},wet=0;
    auto grid=pspconv::surfaceGrid(t,hist,wet);
    namespace f=rendering::psp;
    assert(f::surfaceGridValid(grid.data(),grid.size()));assert(wet==1);
    unsigned n=0;auto* p=f::waterPatches(grid.data(),n);
    assert(n==1 && p->chunk==17 && p->type==7);
    assert(f::waterCell(*p,2,3) && !f::waterCell(*p,3,2));
    assert(p->z[2*9+3]==-5 && p->z[3*9+4]==-2);
    assert(f::waterHeight(*p,2,3,.5f,.5f)==-3.5f);
    float prefixTop=0;std::memcpy(&prefixTop,grid.data()+sizeof(f::SurfaceGridHeader)+256*32+17*4,4);
    assert(prefixTop==-2);
    // An unmasked layer fills only its own rectangle, including at height zero.
    t.waterData[17].layers.clear();l={};l.width=1;l.height=1;l.heights={0,0,0,0};
    t.waterData[0].layers.push_back(l);wet=0;
    grid=pspconv::surfaceGrid(t,hist,wet);p=f::waterPatches(grid.data(),n);
    assert(n==1 && wet==1 && f::waterCell(*p,0,0) && !f::waterCell(*p,0,1));
    l.minHeight=std::numeric_limits<float>::quiet_NaN();t.waterData[0].layers={l};
    grid=pspconv::surfaceGrid(t,hist,wet);p=f::waterPatches(grid.data(),n);assert(n==0);
    std::puts("Kiln water bake regressions passed");
}
