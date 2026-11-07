#pragma once

// PSP: optional host-only instrumentation; callbacks never decide bake results.
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdio>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <utility>

namespace wowee::pspconv {

using BakeProgressCallback = void (*)(void*, const char*, size_t, size_t);
inline thread_local BakeProgressCallback bakeProgressCallback = nullptr;
inline thread_local void* bakeProgressContext = nullptr;

inline void bakeProgress(const char* stage, size_t done = 0, size_t total = 0) noexcept {
    if (bakeProgressCallback) {
        try { bakeProgressCallback(bakeProgressContext, stage, done, total); }
        catch (...) {} // PSP: reporting must never reject an otherwise valid model.
    }
}

class BakeProgressScope {
public:
    BakeProgressScope(BakeProgressCallback callback, void* context) noexcept
        : previousCallback_(bakeProgressCallback), previousContext_(bakeProgressContext) {
        bakeProgressCallback = callback;
        bakeProgressContext = context;
    }
    ~BakeProgressScope() {
        bakeProgressCallback = previousCallback_;
        bakeProgressContext = previousContext_;
    }
    BakeProgressScope(const BakeProgressScope&) = delete;
    BakeProgressScope& operator=(const BakeProgressScope&) = delete;
private:
    BakeProgressCallback previousCallback_;
    void* previousContext_;
};

// PSP: one owner outlives all worker jobs. Stage updates only store state; a
// bounded heartbeat emits whole JSON lines, including during a silent long job.
class BakeProgressMonitor {
    using Clock = std::chrono::steady_clock;
    struct Active {
        int modelId;
        std::string model, stage;
        size_t done = 0, total = 0;
        Clock::time_point stageStart;
    };
public:
    explicit BakeProgressMonitor(std::string phase, size_t total, FILE* output = stderr,
                                 std::chrono::milliseconds interval = std::chrono::seconds(1),
                                 bool immediateJobs = true)
        : phase_(std::move(phase)), total_(total), output_(output), immediateJobs_(immediateJobs),
          start_(Clock::now()) {
        emitLocked();
        heartbeat_ = std::thread([this, interval] {
            std::unique_lock<std::mutex> lock(mutex_);
            while (!changed_.wait_for(lock, interval, [this] { return stopping_; })) emitLocked();
        });
    }
    ~BakeProgressMonitor() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stopping_ = true;
        }
        changed_.notify_one();
        heartbeat_.join();
        emitLocked();
    }
    BakeProgressMonitor(const BakeProgressMonitor&) = delete;
    BakeProgressMonitor& operator=(const BakeProgressMonitor&) = delete;

    void begin(size_t slot, int modelId, const std::string& model) {
        std::lock_guard<std::mutex> lock(mutex_);
        active_.emplace(slot, Active{modelId, model, "loading", 0, 0, Clock::now()});
        if (immediateJobs_) emitLocked();
    }
    void update(size_t slot, const char* stage, size_t done, size_t total) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto found = active_.find(slot);
        if (found == active_.end()) return;
        Active& active = found->second;
        const char* name = stage ? stage : "working";
        if (active.stage != name) {
            active.stage = name;
            active.stageStart = Clock::now();
        }
        active.done = total ? std::min(done, total) : done;
        active.total = total;
    }
    void complete(size_t slot, bool success) noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!active_.erase(slot)) return;
        ++completed_;
        if (!success) ++failed_;
        if (immediateJobs_ || completed_ == total_) emitLocked();
    }
    // PSP: make completed model details available now instead of holding them
    // until the slowest model ends. One stdio call keeps the whole log together.
    void writeLog(const std::string& log) {
        std::lock_guard<std::mutex> lock(mutex_);
        std::fwrite(log.data(), 1, log.size(), output_);
        std::fflush(output_);
    }

private:
    static void quoted(std::string& out, const std::string& value) {
        constexpr char hex[] = "0123456789abcdef";
        out += '"';
        for (unsigned char c : value) {
            if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
            else if (c < 32) { out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15]; }
            else out += static_cast<char>(c);
        }
        out += '"';
    }
    static double seconds(Clock::time_point now, Clock::time_point start) {
        return std::chrono::duration<double>(now - start).count();
    }
    void emitLocked() noexcept {
        try {
            const auto now = Clock::now();
            std::string line = "KILN_PROGRESS {\"phase\":";
            quoted(line, phase_);
            line += ",\"completed\":" + std::to_string(completed_) + ",\"total\":" + std::to_string(total_);
            line += ",\"failed\":" + std::to_string(failed_);
            line += ",\"elapsed_s\":" + std::to_string(seconds(now, start_)) + ",\"active\":[";
            bool first = true;
            for (const auto& item : active_) {
                const Active& active = item.second;
                if (!first) line += ',';
                first = false;
                line += "{\"model_id\":" + std::to_string(active.modelId) + ",\"model\":";
                quoted(line, active.model);
                line += ",\"stage\":";
                quoted(line, active.stage);
                line += ",\"work_done\":" + std::to_string(active.done);
                line += ",\"work_total\":" + std::to_string(active.total);
                line += ",\"stage_elapsed_s\":" + std::to_string(seconds(now, active.stageStart)) + '}';
            }
            line += "]}\n";
            // A single FILE operation cannot interleave with another stdio
            // operation on this stream, including existing converter warnings.
            std::fwrite(line.data(), 1, line.size(), output_);
            std::fflush(output_);
        } catch (...) {} // PSP: allocation/reporting failures do not change data.
    }

    std::string phase_;
    size_t total_, completed_ = 0, failed_ = 0;
    FILE* output_;
    bool immediateJobs_, stopping_ = false;
    Clock::time_point start_;
    std::map<size_t, Active> active_;
    std::mutex mutex_;
    std::condition_variable changed_;
    std::thread heartbeat_;
};

class BakeProgressJob {
public:
    BakeProgressJob(BakeProgressMonitor& monitor, size_t slot, int modelId, const std::string& model)
        : monitor_(monitor), slot_(slot), scope_(callback, this) { monitor_.begin(slot_, modelId, model); }
    ~BakeProgressJob() { complete(false); }
    void complete(bool success) noexcept {
        if (finished_) return;
        monitor_.complete(slot_, success);
        finished_ = true;
    }
    BakeProgressJob(const BakeProgressJob&) = delete;
    BakeProgressJob& operator=(const BakeProgressJob&) = delete;
private:
    static void callback(void* context, const char* stage, size_t done, size_t total) {
        auto& job = *static_cast<BakeProgressJob*>(context);
        job.monitor_.update(job.slot_, stage, done, total);
    }
    BakeProgressMonitor& monitor_;
    size_t slot_;
    bool finished_ = false;
    BakeProgressScope scope_;
};

} // namespace wowee::pspconv
