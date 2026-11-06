// PSP: rebuild animation without redoing textures or renumbering pack records.
#pragma once
#include "skin_bake.hpp"
#include <stdexcept>

namespace wowee::pspconv {

// PSP: synthesized equipment/seat points can extend beyond the source mesh and
// source attachment list. Include their actual retained positions in every
// bake/decimation oracle; the table and original bone indices remain untouched.
inline void appendSkinAttachmentWitnesses(pipeline::M2Model& model,
                                          const std::vector<uint8_t>& oldSkin,
                                          const std::vector<uint8_t>& table) {
    fmt::SkinView old;
    if(!old.init(oldSkin.data(),static_cast<uint32_t>(oldSkin.size()))||table.size()<sizeof(fmt::AttachTable))
        throw std::runtime_error("invalid attachment witnesses");
    fmt::AttachTable header;std::memcpy(&header,table.data(),sizeof(header));
    if(std::memcmp(header.magic,"WATP",4)||header.version!=fmt::kModelFormatVersion||
       header.count>(table.size()-sizeof(header))/sizeof(fmt::AttachPoint))throw std::runtime_error("invalid witness table");
    for(uint32_t i=0;i<header.count;++i) {
        fmt::AttachPoint point;std::memcpy(&point,table.data()+sizeof(header)+i*sizeof(point),sizeof(point));
        if(point.bone==fmt::kNoAttachBone)continue;
        if(point.bone>=old.h->boneCount||old.bones[point.bone].m2Bone>=model.bones.size())
            throw std::runtime_error("attachment witness bone outside skeleton");
        pipeline::M2Attachment witness{};witness.id=point.id;witness.bone=old.bones[point.bone].m2Bone;
        witness.position=glm::vec3(point.position[0],point.position[1],point.position[2]);
        for(int axis=0;axis<3;++axis)if(!std::isfinite(witness.position[axis]))throw std::runtime_error("nonfinite attachment witness");
        if(std::none_of(model.attachments.begin(),model.attachments.end(),[&](const auto& existing) {
            return existing.bone==witness.bone&&existing.position==witness.position;
        }))model.attachments.push_back(witness);
    }
}

inline std::vector<uint8_t> repairSkinnedAnimation(const std::vector<uint8_t>& oldBlob,
                                                 const pipeline::M2Model& model,
                                                 SkinBakeInfo& info, SkinVerifyResult& verification,
                                                 size_t maxAnimBytes = 0) {
    fmt::SkinView old;
    if (!old.init(oldBlob.data(),static_cast<uint32_t>(oldBlob.size())))
        throw std::runtime_error("invalid existing skin");
    const auto& oh = *old.h;
    if(oh.version>=fmt::kSkinPrecisionVersion)throw std::runtime_error("existing v3 geometry requires a full model rebake");
    if(maxAnimBytes)maxAnimBytes=std::max(maxAnimBytes,size_t{oh.sequenceCount}*oh.boneCount*sizeof(fmt::BoneTrack)+size_t{oh.keyCount}*8u);
    using Signature = std::array<int32_t,5>;
    auto signature = [&](const pipeline::M2Vertex& v) {
        Signature out{};
        for (int a=0;a<3;++a) out[a] = std::lround(std::clamp((v.position[a]-oh.center[a])/oh.half[a],-1.0f,1.0f)*32767.0f);
        for (int a=0;a<2;++a) out[3+a] = std::lround(std::clamp((v.texCoords[0][a]-oh.uvMin[a]) /
                    (oh.uvScale[a]*65535.0f/32768.0f),0.0f,1.0f)*65535.0f);
        return out;
    };
    std::map<Signature,std::vector<uint32_t>> candidates;
    for (uint32_t i=0;i<model.vertices.size();++i) {
        bool fits=true;
        for(int a=0;a<3;++a)
            fits &= std::fabs((model.vertices[i].position[a]-oh.center[a])/oh.half[a]) <= 1.00001f;
        if(fits)candidates[signature(model.vertices[i])].push_back(i);
    }
    std::vector<uint32_t> source;
    std::vector<SkinBatchIn> batches(oh.batchCount);
    std::vector<std::vector<uint32_t>> subSources(oh.subBatchCount);
    std::vector<std::vector<std::vector<uint32_t>>> subAlternatives(oh.subBatchCount);
    std::map<uint32_t,std::array<uint8_t,6>> sourceAttributes;
    std::map<uint32_t,std::vector<uint32_t>> sourceAlternatives;
    for (uint32_t b=0;b<oh.subBatchCount;++b) {
        const auto& sb = old.subBatches[b];
        const bool fp = (oh.flags & fmt::kSkinFloatPosition) != 0;
        const int stride = fmt::skinVertexStride(sb.weights,fp);
        for (uint32_t i=0;i<sb.vertexCount;++i) {
            const uint8_t* v = old.vertices(sb)+i*stride;
            Signature sig{};
            if (fp) {
                float xyz[3];std::memcpy(xyz,v+fmt::skinPositionOffset(sb.weights,true),12);
                for(int a=0;a<3;++a)sig[a]=std::lround(std::clamp(xyz[a],-1.0f,1.0f)*32767.0f);
            } else {
                int16_t xyz[3];std::memcpy(xyz,v+fmt::skinPositionOffset(sb.weights,false),6);
                for(int a=0;a<3;++a)sig[a]=xyz[a];
            }
            uint16_t uv[2];std::memcpy(uv,v+((sb.weights+1)&~1),4);sig[3]=uv[0];sig[4]=uv[1];
            const auto found=candidates.find(sig);
            if(found==candidates.end())throw std::runtime_error("packed vertex has no source XYZ/UV match");
            int selected=-1;
            std::vector<uint32_t> matching;
            for(uint32_t vi:found->second) {
                const auto in=skin_detail::influencesOf(model.vertices[vi],model.bones.size());
                bool weightMatch=false;
                // A >8-bone triangle removes influences globally from its
                // shared vertices. The removed bone can still be in this
                // palette because another vertex uses it: try exact subsets.
                for(unsigned mask=1;mask<(1u<<in.n)&&!weightMatch;++mask) {
                    skin_detail::Influences kept;
                    for(int k=0;k<in.n;++k)if(mask&(1u<<k)) {
                        kept.bone[kept.n]=in.bone[k];kept.w[kept.n++]=in.w[k];
                    }
                    uint8_t q[4];skin_detail::quantizeWeights(kept,q);
                    uint8_t packed[fmt::kSkinMaxPalette]{};
                    for(int k=0;k<kept.n;++k)
                        for(int j=0;j<sb.weights;++j)
                            if(old.bones[sb.palette[j]].m2Bone==kept.bone[k])packed[j]+=q[k];
                    weightMatch=std::memcmp(packed,v,sb.weights)==0;
                }
                if(!weightMatch)continue;
                matching.push_back(vi);
                if(selected<0)selected=static_cast<int>(vi);
            }
            if(selected<0)throw std::runtime_error("packed vertex has no source weight match");
            subSources[b].push_back(static_cast<uint32_t>(selected));
            std::array<uint8_t,6> attributes{};std::memcpy(attributes.data(),v+fmt::skinWeightBytes(sb.weights),6);
            sourceAttributes.emplace(selected,attributes);
            auto& alternatives=sourceAlternatives[selected];alternatives.insert(alternatives.end(),matching.begin(),matching.end());
            std::sort(alternatives.begin(),alternatives.end());alternatives.erase(std::unique(alternatives.begin(),alternatives.end()),alternatives.end());
            subAlternatives[b].push_back(std::move(matching));
        }
    }
    for(uint32_t b=0;b<oh.batchCount;++b) {
        batches[b].material=old.batches[b];batches[b].geoset=old.geosets[b];
        for(uint32_t k=0;k<old.batches[b].indexCount;++k) {
            const auto& src=subSources[old.batches[b].indexStart+k];
            source.insert(source.end(),src.begin(),src.end());
            for(size_t i=2;i<src.size();++i)
                if(src[i]!=src[i-1]&&src[i]!=src[i-2]&&src[i-1]!=src[i-2])
                    batches[b].tris.insert(batches[b].tris.end(),{src[i&1?i-1:i-2],src[i&1?i-2:i-1],src[i]});
        }
    }
    std::vector<int> seqs;
    for(uint32_t i=0;i<oh.sequenceCount;++i) {
        int found=-1;
        for(size_t j=0;j<model.sequences.size();++j)
            if(model.sequences[j].id==old.sequences[i].animId && model.sequences[j].variationIndex==old.sequences[i].variation)
                found=static_cast<int>(j);
        if(found<0)throw std::runtime_error("existing sequence absent from source");
        seqs.push_back(found);
    }
    std::vector<uint16_t> keep;
    for(uint32_t i=0;i<oh.boneCount;++i)keep.push_back(old.bones[i].m2Bone);
    const auto& bind=model.vertices;
    int standSeq=-1;
    for(size_t s=0;s<model.sequences.size();++s)
        if(model.sequences[s].id==0&&model.sequences[s].variationIndex==0)standSeq=static_cast<int>(s);
    SkinBakeInfo scratch;
    auto generated=bakeSkinned(model,bind,batches,seqs,standSeq,oh.sampleRate>0?oh.sampleRate:30,scratch,false,&keep,nullptr,maxAnimBytes);
    fmt::SkinView gen;
    if(!gen.init(generated.data(),static_cast<uint32_t>(generated.size())))throw std::runtime_error("invalid generated skeleton");
    if((oh.flags&fmt::kSkinFloatPosition)&&!(gen.h->flags&fmt::kSkinFloatPosition))
        throw std::runtime_error("float-position downgrade requires a full model rebake");
    // PSP: preserve passing sequences and avoid spending RAM on a refinement
    // that cannot fix the dominant packed-weight/position error. Only the
    // existing model's failing sequences compete for its animation budget.
    auto sequenceError=[&](const fmt::SkinView& animation,uint32_t s) {
        fmt::SkinHeader eh=oh;eh.version=animation.h->version;eh.translationScale=animation.h->translationScale;
        fmt::SkinView eval=old;eval.h=&eh;eval.sequences=animation.sequences;eval.tracks=animation.tracks;eval.keys=animation.keys;
        std::vector<fmt::BoneMatrix> pose(oh.boneCount);std::vector<glm::mat4> reference;float error=0;
        std::set<float> fractions={0,.13f,.37f,.5f,.71f,.93f,.999f,1};
        for(int k=1;k<6;++k)fractions.insert(k/6.0f);
        for(float f:fractions) {
            const float time=old.sequences[s].durationMs*f;fmt::evaluatePose(eval,s,time,pose.data());
            poseBones(model,seqs[s],time,reference);size_t emitted=0;
            for(uint32_t b=0;b<oh.batchCount;++b)
                for(uint32_t k=0;k<old.batches[b].indexCount;++k) {
                    const auto& sb=old.subBatches[old.batches[b].indexStart+k];
                    const int stride=fmt::skinVertexStride(sb.weights,(oh.flags&fmt::kSkinFloatPosition)!=0);
                    for(uint32_t i=0;i<sb.vertexCount;++i,++emitted) {
                        glm::vec3 want;const auto& v=model.vertices[source[emitted]];
                        if(!skinVertex(v,reference,want,nullptr))want=v.position;
                        error=std::max(error,glm::length(geSkin(eval,pose,sb,old.vertices(sb)+i*stride)-want));
                    }
                }
            for(const auto& attachment:model.attachments) {
                const auto it=std::find(keep.begin(),keep.end(),attachment.bone);if(it==keep.end())continue;
                const auto& b=pose[static_cast<size_t>(it-keep.begin())];const auto p=attachment.position;
                glm::vec3 actual;for(int r=0;r<3;++r)actual[r]=b.m[r]*p.x+b.m[3+r]*p.y+b.m[6+r]*p.z+b.m[9+r];
                const auto want=glm::vec3(reference[attachment.bone]*glm::vec4(p,1));
                error=std::max(error,glm::length(actual-want));
            }
        }
        return error;
    };
    if(gen.h->version!=fmt::kSkinTimedVersion) {
    std::vector<uint32_t> intervals(oh.sequenceCount);std::vector<float> benefit(oh.sequenceCount);
    for(uint32_t s=0;s<oh.sequenceCount;++s) {
        intervals[s]=old.sequences[s].frameCount-1;
        if(gen.sequences[s].frameCount<=old.sequences[s].frameCount)continue;
        // Wide-scale positions need the format repair first; the existing
        // quantized geometry cannot assess their sampling independently.
        if(gen.h->flags&fmt::kSkinFloatPosition) {
            intervals[s]=gen.sequences[s].frameCount-1;benefit[s]=1;continue;
        }
        const float before=sequenceError(old,s),after=skinSequenceError(gen,s,model,seqs[s],bind,scratch.emittedSource);
        if(before>.02f&&(after<=.02f||after<before*.8f)) {
            intervals[s]=gen.sequences[s].frameCount-1;
            benefit[s]=(before-after)/(gen.sequences[s].frameCount-old.sequences[s].frameCount);
        }
    }
    auto regenerate=[&]() {
        generated=bakeSkinned(model,bind,batches,seqs,standSeq,oh.sampleRate>0?oh.sampleRate:30,scratch,false,&keep,&intervals,maxAnimBytes);
        if(!gen.init(generated.data(),static_cast<uint32_t>(generated.size())))throw std::runtime_error("invalid budgeted skin");
    };
    regenerate();
    while(maxAnimBytes&&scratch.animBytes>maxAnimBytes) {
        int discard=-1;
        for(uint32_t s=0;s<oh.sequenceCount;++s)
            if(intervals[s]>old.sequences[s].frameCount-1u && (discard<0||benefit[s]<benefit[discard]))discard=s;
        if(discard<0)throw std::runtime_error("required base animation exceeds memory budget");
        intervals[discard]=old.sequences[discard].frameCount-1;benefit[discard]=0;regenerate();
    }
    } else if(maxAnimBytes&&scratch.animBytes>maxAnimBytes)throw std::runtime_error("required timed animation "+
        std::to_string(scratch.animBytes)+" exceeds memory budget "+std::to_string(maxAnimBytes));
    const bool precisionGeometry=gen.h->version>=fmt::kSkinPrecisionVersion;
    const auto& geometry=precisionGeometry?gen:old;
    std::vector<uint16_t> stableOrder;
    for(uint32_t b=0;b<oh.boneCount;++b) {
        uint16_t found=0;while(found<gen.h->boneCount&&gen.bones[found].m2Bone!=old.bones[b].m2Bone)++found;
        if(found==gen.h->boneCount)throw std::runtime_error("old attachment bone missing from repaired skeleton");
        stableOrder.push_back(found);
    }
    for(uint16_t b=0;b<gen.h->boneCount;++b)
        if(std::find(stableOrder.begin(),stableOrder.end(),b)==stableOrder.end())stableOrder.push_back(b);
    std::vector<uint16_t> stableIndex(gen.h->boneCount);
    for(uint16_t i=0;i<stableOrder.size();++i)stableIndex[stableOrder[i]]=i;
    std::vector<fmt::SkinBone> stableBones(stableOrder.size());
    std::vector<fmt::BoneTrack> stableTracks(stableOrder.size()*oh.sequenceCount);
    for(size_t i=0;i<stableOrder.size();++i) {
        auto bone=gen.bones[stableOrder[i]];
        if(bone.parent>=0)bone.parent=static_cast<int16_t>(stableIndex[bone.parent]);
        if(bone.parent>=static_cast<int>(i))throw std::runtime_error("repaired bone parent is not before child");
        stableBones[i]=bone;
        for(uint32_t s=0;s<oh.sequenceCount;++s)stableTracks[s*stableOrder.size()+i]=gen.tracks[s*gen.h->boneCount+stableOrder[i]];
    }
    if(!precisionGeometry&&(stableBones.size()!=oh.boneCount||std::memcmp(stableBones.data(),old.bones,oh.boneCount*sizeof(fmt::SkinBone))))
        throw std::runtime_error("rebuilt skeleton differs from existing skeleton");
    fmt::SkinHeader h=precisionGeometry?*gen.h:oh;
    h.version=gen.h->version;h.translationScale=gen.h->translationScale;
    constexpr uint16_t generatedFlags=fmt::kSkinFloatPosition|fmt::kSkinHasVirtualPalette|fmt::kSkinSlerpRotation|fmt::kSkinFloatRotation;
    h.flags=static_cast<uint16_t>((oh.flags&~generatedFlags)|(gen.h->flags&generatedFlags));
    h.boneCount=static_cast<uint16_t>(stableBones.size());
    h.standHeight=oh.standHeight;h.radius=oh.radius;
    std::copy_n(oh.uvMin,2,h.uvMin);std::copy_n(oh.uvScale,2,h.uvScale);
    h.reserved[0]=oh.reserved[0];h.reserved[1]=h.reserved[2]=0;
    h.keyCount=gen.h->keyCount;
    std::vector<uint8_t> blob(sizeof(h)+(h.version==fmt::kSkinTimedVersion?sizeof(fmt::SkinTimingExtension):0));
    auto section=[&](const void* data,size_t bytes,size_t align=4) {
        alignTo(blob,align);const auto at=static_cast<uint32_t>(blob.size());
        const auto* p=static_cast<const uint8_t*>(data);blob.insert(blob.end(),p,p+bytes);return at;
    };
    h.boneOffset=section(stableBones.data(),stableBones.size()*sizeof(fmt::SkinBone));
    h.sequenceOffset=section(gen.sequences,oh.sequenceCount*sizeof(fmt::AnimSequence));
    h.batchOffset=section(geometry.batches,oh.batchCount*sizeof(fmt::ModelBatch));
    h.geosetOffset=section(old.geosets,oh.batchCount*2u);
    std::vector<fmt::SkinSubBatch> subs(geometry.subBatches,geometry.subBatches+geometry.h->subBatchCount);
    h.subBatchCount=static_cast<uint16_t>(subs.size());
    if(precisionGeometry)for(auto& sb:subs)for(int j=0;j<sb.weights;++j)sb.palette[j]=static_cast<uint8_t>(stableIndex[sb.palette[j]]);
    h.subBatchOffset=section(subs.data(),subs.size()*sizeof(fmt::SkinSubBatch));
    h.trackOffset=section(stableTracks.data(),stableTracks.size()*sizeof(fmt::BoneTrack));
    h.keyOffset=section(gen.keys,h.keyCount*8u);
    if(h.flags&fmt::kSkinHasVirtualPalette) {
        fmt::SkinPaletteHeader ph{};std::memcpy(ph.magic,"WVPL",4);ph.version=1;ph.slots=fmt::kSkinMaxPalette;ph.subBatchCount=h.subBatchCount;
        std::vector<fmt::SkinPaletteBlend> table(h.subBatchCount*fmt::kSkinMaxPalette);
        std::copy_n(gen.virtualPalettes,table.size(),table.data());
        for(auto& entry:table)for(int k=0;k<4;++k)if(entry.weights[k])entry.bones[k]=static_cast<uint8_t>(stableIndex[entry.bones[k]]);
        std::vector<uint8_t> data;const auto* p=reinterpret_cast<const uint8_t*>(&ph);data.insert(data.end(),p,p+sizeof(ph));
        p=reinterpret_cast<const uint8_t*>(table.data());data.insert(data.end(),p,p+table.size()*sizeof(table[0]));
        h.reserved[2]=section(data.data(),data.size());
    }
    if(h.version==fmt::kSkinTimedVersion) {
        std::vector<uint8_t> table={'W','T','I','M'};put<uint32_t>(table,1);put<uint32_t>(table,h.sequenceCount);
        table.resize(12+h.sequenceCount*8u,0);const auto timingOffset=section(table.data(),table.size());
        for(uint32_t s=0;s<h.sequenceCount;++s) {
            const uint32_t count=gen.sequences[s].frameCount;const auto* times=gen.sequenceTimes(s);
            if(!times)throw std::runtime_error("missing generated frame times");
            const auto offset=section(times,count*4u);
            std::memcpy(blob.data()+timingOffset+12+s*8,&offset,4);std::memcpy(blob.data()+timingOffset+16+s*8,&count,4);
        }
        std::memcpy(blob.data()+sizeof(h),&timingOffset,4);
    }
    alignTo(blob,16);const size_t verticesBegin=blob.size();
    const bool oldFloat=(oh.flags&fmt::kSkinFloatPosition)!=0,newFloat=(h.flags&fmt::kSkinFloatPosition)!=0;
    size_t generatedVertex=0;
    for(size_t b=0;b<subs.size();++b) {
        auto& sb=subs[b];const int oldStride=fmt::skinVertexStride(sb.weights,oldFloat);
        const int stride=fmt::skinVertexStride(sb.weights,newFloat,sb.reserved);
        std::vector<uint8_t> vertices(sb.vertexCount*stride);
        for(uint32_t i=0;i<sb.vertexCount;++i) {
            auto* to=vertices.data()+i*stride;
            if(precisionGeometry) {
                const auto* from=gen.vertices(gen.subBatches[b])+i*stride;std::memcpy(to,from,stride);
                const auto vi=scratch.emittedSource[generatedVertex++];
                const auto attr=sourceAttributes.find(vi);if(attr==sourceAttributes.end())throw std::runtime_error("new vertex has no original attributes");
                std::memcpy(to+fmt::skinWeightBytes(sb.weights,sb.reserved),attr->second.data(),6);
            } else {
              const auto* from=old.vertices(old.subBatches[b])+i*oldStride;
              if(!newFloat)std::memcpy(to,from,stride);
            else {
                std::memcpy(to,from,((sb.weights+1)&~1)+6);
                const auto& p=model.vertices[subSources[b][i]].position;float xyz[3];
                for(int a=0;a<3;++a)xyz[a]=(p[a]-h.center[a])/h.half[a];
                std::memcpy(to+fmt::skinPositionOffset(sb.weights,true),xyz,12);
            }
            }
        }
        sb.vertexOffset=section(vertices.data(),vertices.size(),16);
    }
    h.vertexBytes=static_cast<uint32_t>(blob.size()-verticesBegin);
    std::memcpy(blob.data()+h.subBatchOffset,subs.data(),subs.size()*sizeof(fmt::SkinSubBatch));
    if(oh.flags&fmt::kSkinHasEvents) {
        uint32_t n=0;fmt::skinFootsteps(&oh,static_cast<uint32_t>(oldBlob.size()),0,n);
        const uint32_t at=oh.reserved[1];
        if(at>oldBlob.size()||oldBlob.size()-at<sizeof(fmt::SkinEventTable))throw std::runtime_error("invalid event table");
        const auto* table=reinterpret_cast<const fmt::SkinEventTable*>(oldBlob.data()+at);
        const size_t bytes=sizeof(*table)+((2u*(oh.sequenceCount+1u)+3u)&~3u)+table->count*4u;
        if(bytes>oldBlob.size()-at)throw std::runtime_error("truncated event table");
        h.reserved[1]=section(table,bytes);
    }
    std::memcpy(blob.data(),&h,sizeof(h));
    info=scratch;if(!precisionGeometry)info.emittedSource=std::move(source);info.vertexBytes=h.vertexBytes;
    verification=verifySkinned(blob,model,model.vertices,seqs,info,batches,nullptr,nullptr);
    // PSP: quantization can collapse nearby source vertices with identical UVs
    // and weights. Do not pretend the first candidate is the exact inverse:
    // check the repaired point against EVERY possible source at every verifier
    // time. A wide-position reconstruction is accepted only when that entire
    // uncertainty stays inside the existing (unchanged) 0.020-yard limit.
    fmt::SkinView repaired;
    if(!repaired.init(blob.data(),static_cast<uint32_t>(blob.size())))throw std::runtime_error("invalid repaired skin");
    verification.samples=verification.probeTimes=0;verification.sumSq=0;
    for(uint32_t s=0;s<h.sequenceCount;++s) {
        size_t count=0,times=0;double sum=0;std::string worst;
        const auto* pinned=s<info.verificationTimes.size()?&info.verificationTimes[s]:nullptr;
        const float error=skinSequenceError(repaired,s,model,seqs[s],model.vertices,info.emittedSource,true,nullptr,&count,&times,&sum,&worst,pinned);
        verification.samples+=count;verification.probeTimes+=times;verification.sumSq+=sum;
        if(error>verification.maxErr){verification.maxErr=error;verification.worst=std::move(worst);}
    }
    std::vector<fmt::BoneMatrix> pose(h.boneCount);std::vector<glm::mat4> ref;
    float ambiguityError=0;
    for(uint32_t s=0;s<h.sequenceCount;++s)
        for(float time:skinProbeTimes(repaired,s,model,seqs[s],true,
                s<info.verificationTimes.size()?&info.verificationTimes[s]:nullptr)) {
            fmt::evaluatePose(repaired,s,time,pose.data());poseBones(model,seqs[s],time,ref);
            size_t emitted=0;
            for(size_t b=0;b<subs.size();++b) {
                const auto& sb=repaired.subBatches[b];const int stride=fmt::skinVertexStride(sb.weights,newFloat,sb.reserved);
                for(uint32_t i=0;i<sb.vertexCount;++i) {
                    const auto& possible=precisionGeometry?sourceAlternatives.at(info.emittedSource[emitted++]):subAlternatives[b][i];
                    if(possible.size()<2)continue;
                    const auto point=geSkin(repaired,pose,sb,repaired.vertices(sb)+i*stride);
                    for(uint32_t vi:possible) {
                        ++verification.uncertaintySamples;
                        glm::vec3 want;if(!skinVertex(model.vertices[vi],ref,want,nullptr))want=model.vertices[vi].position;
                        const float error=glm::length(point-want);ambiguityError=std::max(ambiguityError,error);
                        if(error>verification.maxErr) {
                            verification.maxErr=error;
                            verification.worst="ambiguous source vertex "+std::to_string(vi)+" anim "+
                                std::to_string(old.sequences[s].animId)+" t="+std::to_string(time);
                        }
                    }
                }
            }
        }
    if((newFloat||precisionGeometry) && ambiguityError>.02f)throw std::runtime_error("source reconstruction uncertainty exceeds 0.020 yd");
    return blob;
}

// PSP: check the actual retained WATP points, not a newly synthesized table.
inline SkinVerifyResult verifySkinAttachmentTable(const std::vector<uint8_t>& skin,
                                                  const std::vector<uint8_t>& table,
                                                  const pipeline::M2Model& model) {
    fmt::SkinView v;
    if(!v.init(skin.data(),static_cast<uint32_t>(skin.size())) || table.size()<sizeof(fmt::AttachTable))
        throw std::runtime_error("invalid attachment inputs");
    fmt::AttachTable header;std::memcpy(&header,table.data(),sizeof(header));
    if(std::memcmp(header.magic,"WATP",4)||header.version!=fmt::kModelFormatVersion ||
       header.count>(table.size()-sizeof(header))/sizeof(fmt::AttachPoint))throw std::runtime_error("invalid WATP table");
    SkinVerifyResult result;
    std::vector<fmt::BoneMatrix> pose(v.h->boneCount);std::vector<glm::mat4> reference;
    for(uint32_t s=0;s<v.h->sequenceCount;++s) {
        int seq=-1;
        for(size_t i=0;i<model.sequences.size();++i)
            if(model.sequences[i].id==v.sequences[s].animId&&model.sequences[i].variationIndex==v.sequences[s].variation)
                seq=static_cast<int>(i);
        if(seq<0)throw std::runtime_error("attachment sequence missing");
        // PSP: the same authored-key and baked-interval oracle used for the
        // repaired mesh also covers retained points between old coarse probes.
        for(float t:skinProbeTimes(v,s,model,seq,true)) {
            ++result.probeTimes;
            fmt::evaluatePose(v,s,t,pose.data());poseBones(model,seq,t,reference);
            for(uint32_t i=0;i<header.count;++i) {
                fmt::AttachPoint point;std::memcpy(&point,table.data()+sizeof(header)+i*sizeof(point),sizeof(point));
                if(point.bone==fmt::kNoAttachBone)continue;
                if(point.bone>=v.h->boneCount||v.bones[point.bone].m2Bone>=reference.size())
                    throw std::runtime_error("attachment bone outside skeleton");
                const auto& b=pose[point.bone];const glm::vec3 p(point.position[0],point.position[1],point.position[2]);
                glm::vec3 actual;
                for(int r=0;r<3;++r)actual[r]=b.m[r]*p.x+b.m[3+r]*p.y+b.m[6+r]*p.z+b.m[9+r];
                const auto expected=glm::vec3(reference[v.bones[point.bone].m2Bone]*glm::vec4(p,1));
                const float error=glm::length(expected-actual);
                ++result.samples;result.sumSq+=error*error;
                if(error>result.maxErr) {
                    result.maxErr=error;result.worst="point "+std::to_string(point.id)+" anim "+
                        std::to_string(v.sequences[s].animId)+" t="+std::to_string(t);
                }
            }
        }
    }
    return result;
}
} // namespace wowee::pspconv
