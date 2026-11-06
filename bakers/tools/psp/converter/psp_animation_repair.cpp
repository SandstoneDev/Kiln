// PSP: local selective animation rebuild. No textures, DBCs, pack rewriting or network.
// Usage: psp_animation_repair EXTRACTED SOURCE.pak MODELS.tsv NEW_OUTPUT_DIR
// TSV: n_<existing record id><TAB>relative/model/path.m2 (one per line).
// Build with m2_loader.cpp, logger.cpp and meshoptimizer, like psp_char_conv.
#include "skin_animation_repair.hpp"
#include <filesystem>

using namespace wowee;
using namespace wowee::pspconv;

int main(int argc,char** argv) {
    const bool attachments=argc==6&&std::string(argv[5])=="--verify-attachments";
    if(argc!=5&&!attachments){std::fprintf(stderr,"usage: psp_animation_repair EXTRACTED SOURCE.pak MODELS.tsv OUTPUT_DIR [--verify-attachments]\n");return 2;}
    try {
        const std::filesystem::path root=argv[1],out=argv[4];
        if(!attachments&&std::filesystem::exists(out))throw std::runtime_error("output directory must not exist");
        if(attachments&&std::filesystem::exists(out/"attachment_verification.tsv"))throw std::runtime_error("attachment report already exists");
        std::ifstream pak(argv[2],std::ios::binary),list(argv[3]);
        if(!pak||!list)throw std::runtime_error("source pack or model list cannot open");
        pak.seekg(0,std::ios::end);const auto packSize=static_cast<uint64_t>(pak.tellg());pak.seekg(0);
        uint32_t header[4];pak.read(reinterpret_cast<char*>(header),16);
        if(!pak||std::memcmp(header,"WPAK",4)||header[1]!=1||header[2]>2000000||header[3]!=16||
           16ull+64ull*header[2]>packSize)throw std::runtime_error("invalid pack header");
        struct Record{uint32_t offset,size;};std::map<std::string,Record> records;
        for(uint32_t i=0;i<header[2];++i) {
            char name[48];uint32_t data[4];pak.read(name,48);pak.read(reinterpret_cast<char*>(data),16);
            const auto* end=static_cast<const char*>(std::memchr(name,0,48));
            if(!pak||!end||data[0]>packSize||data[1]>packSize-data[0])throw std::runtime_error("invalid pack record");
            if(!records.emplace(std::string(name,static_cast<size_t>(end-name)),Record{data[0],data[1]}).second)
                throw std::runtime_error("duplicate pack record");
        }
        struct Job{std::string name,path;};std::vector<Job> jobs;std::set<std::string> seen;
        for(std::string line;std::getline(list,line);) {
            if(!line.empty()&&line.back()=='\r')line.pop_back();
            if(line.empty()||line[0]=='#')continue;
            const auto tab=line.find('\t');if(tab==std::string::npos)throw std::runtime_error("expected name TAB model path");
            Job job{line.substr(0,tab),lower(line.substr(tab+1))};
            if(job.name.rfind("n_",0)!=0 || job.name.find_first_not_of("n_0123456789")!=std::string::npos ||
               !records.count(job.name)||!seen.insert(job.name).second)throw std::runtime_error("invalid/duplicate record name");
            std::filesystem::path relative=job.path;
            if(relative.is_absolute()||relative.has_root_name())throw std::runtime_error("model path must be relative");
            for(const auto& part:relative)if(part=="..")throw std::runtime_error("model path escapes extracted root");
            jobs.push_back(std::move(job));
        }
        if(jobs.empty())throw std::runtime_error("empty model list");
        if(!attachments)std::filesystem::create_directories(out);
        std::ofstream receipt(out/(attachments?"attachment_verification.tsv":"results.tsv"));
        receipt<<"entry\tbytes_before\tbytes_after\tanim_bytes\tmax_error\tvertex_samples\tworst\tprobe_times\tsource_candidate_checks\n";
        std::vector<std::string> completed;int over=0,failed=0;
        for(const auto& job:jobs) {
          try {
            const auto& rec=records.at(job.name);std::vector<uint8_t> oldBlob(rec.size),m2;
            pak.clear();pak.seekg(rec.offset);pak.read(reinterpret_cast<char*>(oldBlob.data()),rec.size);
            pipeline::M2Model model;
            if(!pak||!readFile((root/job.path).string(),m2)||
               !loadM2(m2,(root/lower(pipeline::skinPathForM2(job.path))).string(),model))
                throw std::runtime_error("cannot load "+job.path);
            fmt::SkinView old;if(!old.init(oldBlob.data(),rec.size))throw std::runtime_error("invalid "+job.name);
            for(uint32_t s=0;s<old.h->sequenceCount;++s) {
                int seq=-1;
                for(size_t k=0;k<model.sequences.size();++k)
                    if(model.sequences[k].id==old.sequences[s].animId && model.sequences[k].variationIndex==old.sequences[s].variation)
                        seq=static_cast<int>(k);
                if(seq<0)throw std::runtime_error("missing source sequence");
                if(!(model.sequences[seq].flags&0x20)) {
                    std::vector<uint8_t> anim;
                    if(!readFile((root/lower(animPath(job.path,model.sequences[seq].id,model.sequences[seq].variationIndex))).string(),anim))
                        throw std::runtime_error("missing external animation for "+job.name);
                    pipeline::M2Loader::loadAnimFile(m2,anim,seq,model);
                }
            }
            const auto pointRec=records.find("p_"+job.name.substr(2));
            std::vector<uint8_t> points;
            if(pointRec!=records.end()) {
                points.resize(pointRec->second.size);pak.clear();pak.seekg(pointRec->second.offset);
                pak.read(reinterpret_cast<char*>(points.data()),points.size());if(!pak)throw std::runtime_error("cannot read points");
            }
            SkinBakeInfo info;SkinVerifyResult verification;
            if(attachments) {
                std::vector<uint8_t> skin;
                if(!readFile((out/(job.name+".bin")).string(),skin))throw std::runtime_error("replacement absent");
                if(pointRec==records.end())throw std::runtime_error("source attachment table absent");
                verification=verifySkinAttachmentTable(skin,points,model);
                receipt<<job.name<<"\t0\t0\t0\t"<<verification.maxErr<<'\t'<<verification.samples<<'\t'<<verification.worst<<'\t'<<verification.probeTimes<<"\t0\n";receipt.flush();
                if(verification.maxErr>.02)++over;
                completed.push_back(job.name);continue;
            }
            if(pointRec!=records.end())appendSkinAttachmentWitnesses(model,oldBlob,points);
            const bool creature=std::count(job.name.begin(),job.name.end(),'_')==1;
            auto blob=repairSkinnedAnimation(oldBlob,model,info,verification,creature?384u*1024u:0u);
            const auto output=out/(job.name+".bin");std::ofstream file(output,std::ios::binary);
            file.write(reinterpret_cast<const char*>(blob.data()),blob.size());file.close();
            if(!file)throw std::runtime_error("cannot write replacement");
            completed.push_back(job.name);
            receipt<<job.name<<'\t'<<oldBlob.size()<<'\t'<<blob.size()<<'\t'<<info.animBytes<<'\t'<<verification.maxErr<<'\t'
                   <<verification.samples<<'\t'<<verification.worst<<'\t'<<verification.probeTimes<<'\t'<<verification.uncertaintySamples<<'\n';receipt.flush();
            std::printf("%s %zu -> %zu bytes, max %.7f yd, %s\n",job.name.c_str(),oldBlob.size(),blob.size(),verification.maxErr,verification.worst.c_str());
            if(verification.maxErr>.02)++over;
          } catch(const std::exception& e) {
            ++failed;receipt<<job.name<<"\tERROR\t"<<e.what()<<'\n';receipt.flush();
            std::fprintf(stderr,"%s: %s\n",job.name.c_str(),e.what());
          }
        }
        if(!attachments) {
            std::ofstream manifest(out/"manifest.json");manifest<<"{\"entries\":[";
            for(size_t i=0;i<completed.size();++i)manifest<<(i?",":"")<<'"'<<completed[i]<<'"';
            manifest<<"]}\n";manifest.close();if(!manifest)throw std::runtime_error("cannot write manifest");
        }
        std::printf("%s %zu entries; %d failed; %d remain above 0.020 yd.%s\n",attachments?"verified":"rebuilt",
                     completed.size(),failed,over,attachments?"":" Pin source_sha256 before patching.");
        return failed?3:over?4:0;
    } catch(const std::exception& e){std::fprintf(stderr,"animation repair: %s\n",e.what());return 3;}
}
