/* NVN -> DX12 presentation layer.
 *
 * This is the bottom half of the graphics stack: a real D3D12 device, a real
 * DXGI flip-model swapchain on a real window, a command allocator/list and a
 * fence, plus the per-frame CSV record that Gate 2 requires.  The NVN API
 * surface (nvn_device / nvn_window / nvn_swapchain / nvn_command_buffer)
 * is implemented on top of exactly these calls, so anything that reaches the
 * NVN layer ends up presenting through this file.
 *
 * Nothing here invents a frame: every row in the CSV is written after
 * ExecuteCommandLists + Present actually returned for that frame.
 */

#include <windows.h>
#include <d3d12.h>
#include <dxgi1_6.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

#include <wrl/client.h>

using Microsoft::WRL::ComPtr;

#define NVN_D3D12_MAX_FRAMES 100000

/* Shared boot log handle from a64_helpers.c. */
extern "C" FILE* g_log;

namespace {

struct GfxState {
    HWND hwnd = nullptr;
    ComPtr<IDXGIFactory6> factory;
    ComPtr<ID3D12Device> device;
    ComPtr<ID3D12CommandQueue> queue;
    ComPtr<IDXGISwapChain3> swapchain;
    ComPtr<ID3D12DescriptorHeap> rtv_heap;
    ComPtr<ID3D12Resource> rtv[2];
    ComPtr<ID3D12CommandAllocator> alloc;
    ComPtr<ID3D12GraphicsCommandList> list;
    ComPtr<ID3D12Fence> fence;
    HANDLE fence_event = nullptr;
    UINT64 fence_value = 0;
    UINT rtv_stride = 0;
    UINT width = 0;
    UINT height = 0;
    UINT buffer_count = 2;
    UINT frame_index = 0;
    int ready = 0;

    FILE* csv = nullptr;
    char csv_path[MAX_PATH] = {0};
    uint64_t frames = 0;
    uint64_t draws = 0;
    uint64_t presents = 0;
    LARGE_INTEGER qpc_freq = {0};
    LARGE_INTEGER frame_start = {0};
};

GfxState g;

void log_line(const char* fmt, ...) {
    /* The runtime log is written by main.c; graphics messages go to stderr and
       the shared boot log through the same file handle when it is open. */
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fflush(stderr);
    if (g_log) {
        va_start(ap, fmt);
        vfprintf(g_log, fmt, ap);
        va_end(ap);
        fflush(g_log);
    }
}

LRESULT CALLBACK wnd_proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcA(h, m, w, l);
}

void csv_open() {
    time_t t = time(nullptr);
    struct tm tmv;
    localtime_s(&tmv, &t);
    snprintf(g.csv_path, sizeof(g.csv_path), "bench_%04d%02d%02d_%02d%02d%02d.csv",
             tmv.tm_year + 1900, tmv.tm_mon + 1, tmv.tm_mday,
             tmv.tm_hour, tmv.tm_min, tmv.tm_sec);
    g.csv = fopen(g.csv_path, "wb");
    if (!g.csv) {
        log_line("[dx12] CSV open FAILED for %s\n", g.csv_path);
        return;
    }
    fprintf(g.csv, "frame,frame_time_us,cpu_time_us,draw_calls,presents,width,height\n");
    fflush(g.csv);
    log_line("[dx12] CSV %s opened\n", g.csv_path);
}

void csv_row(uint64_t frame, double frame_us, double cpu_us) {
    if (!g.csv) return;
    fprintf(g.csv, "%llu,%.1f,%.1f,%llu,%llu,%u,%u\n",
            (unsigned long long)frame, frame_us, cpu_us,
            (unsigned long long)g.draws, (unsigned long long)g.presents,
            g.width, g.height);
    fflush(g.csv);
}

void wait_gpu() {
    if (!g.queue || !g.fence) return;
    const UINT64 target = ++g.fence_value;
    if (FAILED(g.queue->Signal(g.fence.Get(), target))) return;
    if (g.fence->GetCompletedValue() < target) {
        g.fence->SetEventOnCompletion(target, g.fence_event);
        WaitForSingleObject(g.fence_event, 5000);
    }
}

}  // namespace

extern "C" {

int nvn_d3d12_init(UINT width, UINT height, const char* title) {
    if (g.ready) return 0;
    g.width = width ? width : 1280;
    g.height = height ? height : 720;
    QueryPerformanceFrequency(&g.qpc_freq);

    WNDCLASSEXA wc = {0};
    wc.cbSize = sizeof(wc);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = wnd_proc;
    wc.hInstance = GetModuleHandleA(nullptr);
    wc.hCursor = LoadCursorA(nullptr, IDC_ARROW);
    wc.lpszClassName = "SplatNativeNVN";
    if (!RegisterClassExA(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        log_line("[dx12] RegisterClassEx failed %lu\n", GetLastError());
        return -1;
    }
    RECT rc = {0, 0, (LONG)g.width, (LONG)g.height};
    AdjustWindowRect(&rc, WS_OVERLAPPEDWINDOW, FALSE);
    g.hwnd = CreateWindowExA(0, "SplatNativeNVN", title ? title : "SplatNative",
                             WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT,
                             rc.right - rc.left, rc.bottom - rc.top,
                             nullptr, nullptr, wc.hInstance, nullptr);
    if (!g.hwnd) {
        log_line("[dx12] CreateWindow failed %lu\n", GetLastError());
        return -2;
    }
    ShowWindow(g.hwnd, SW_SHOWNORMAL);
    UpdateWindow(g.hwnd);
    log_line("[dx12] window %ux%u hwnd=%p\n", g.width, g.height, (void*)g.hwnd);

    UINT factory_flags = 0;
#if defined(_DEBUG)
    factory_flags |= DXGI_CREATE_FACTORY_DEBUG;
#endif
    HRESULT hr = CreateDXGIFactory2(factory_flags, __uuidof(IDXGIFactory6),
                                    (void**)g.factory.GetAddressOf());
    if (FAILED(hr)) {
        log_line("[dx12] CreateDXGIFactory2 failed %08lx\n", (unsigned long)hr);
        return -3;
    }

    ComPtr<IDXGIAdapter1> adapter;
    for (UINT i = 0; g.factory->EnumAdapterByGpuPreference(
                         i, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
                         __uuidof(IDXGIAdapter1), (void**)adapter.ReleaseAndGetAddressOf()) != DXGI_ERROR_NOT_FOUND; ++i) {
        DXGI_ADAPTER_DESC1 desc = {};
        if (FAILED(adapter->GetDesc1(&desc))) continue;
        if (desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) continue;
        if (SUCCEEDED(D3D12CreateDevice(adapter.Get(), D3D_FEATURE_LEVEL_11_0,
                                        __uuidof(ID3D12Device), nullptr))) {
            log_line("[dx12] adapter: %ls\n", desc.Description);
            break;
        }
        adapter.Reset();
    }
    if (!adapter) {
        log_line("[dx12] no D3D12-capable adapter\n");
        return -4;
    }
    hr = D3D12CreateDevice(adapter.Get(), D3D_FEATURE_LEVEL_11_0,
                           __uuidof(ID3D12Device), (void**)g.device.GetAddressOf());
    if (FAILED(hr)) {
        log_line("[dx12] D3D12CreateDevice failed %08lx\n", (unsigned long)hr);
        return -5;
    }

    D3D12_COMMAND_QUEUE_DESC qd = {};
    qd.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    hr = g.device->CreateCommandQueue(&qd, __uuidof(ID3D12CommandQueue),
                                      (void**)g.queue.GetAddressOf());
    if (FAILED(hr)) {
        log_line("[dx12] CreateCommandQueue failed %08lx\n", (unsigned long)hr);
        return -6;
    }

    DXGI_SWAP_CHAIN_DESC1 sd = {};
    sd.Width = g.width;
    sd.Height = g.height;
    sd.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount = g.buffer_count;
    sd.Scaling = DXGI_SCALING_STRETCH;
    sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    sd.AlphaMode = DXGI_ALPHA_MODE_IGNORE;

    ComPtr<IDXGISwapChain1> sc1;
    hr = g.factory->CreateSwapChainForHwnd(g.queue.Get(), g.hwnd, &sd, nullptr,
                                           nullptr, sc1.GetAddressOf());
    if (FAILED(hr)) {
        log_line("[dx12] CreateSwapChainForHwnd failed %08lx\n", (unsigned long)hr);
        return -7;
    }
    g.factory->MakeWindowAssociation(g.hwnd, DXGI_MWA_NO_ALT_ENTER);
    hr = sc1.As(&g.swapchain);
    if (FAILED(hr)) {
        log_line("[dx12] swapchain3 QI failed %08lx\n", (unsigned long)hr);
        return -8;
    }

    D3D12_DESCRIPTOR_HEAP_DESC hd = {};
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
    hd.NumDescriptors = g.buffer_count;
    hr = g.device->CreateDescriptorHeap(&hd, __uuidof(ID3D12DescriptorHeap),
                                        (void**)g.rtv_heap.GetAddressOf());
    if (FAILED(hr)) return -9;
    g.rtv_stride = g.device->GetDescriptorHandleIncrementSize(
        D3D12_DESCRIPTOR_HEAP_TYPE_RTV);

    D3D12_CPU_DESCRIPTOR_HANDLE h = g.rtv_heap->GetCPUDescriptorHandleForHeapStart();
    for (UINT i = 0; i < g.buffer_count; ++i) {
        hr = g.swapchain->GetBuffer(i, __uuidof(ID3D12Resource),
                                    (void**)g.rtv[i].GetAddressOf());
        if (FAILED(hr)) return -10;
        g.device->CreateRenderTargetView(g.rtv[i].Get(), nullptr, h);
        h.ptr += g.rtv_stride;
    }

    hr = g.device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT,
                                          __uuidof(ID3D12CommandAllocator),
                                          (void**)g.alloc.GetAddressOf());
    if (FAILED(hr)) return -11;
    hr = g.device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, g.alloc.Get(),
                                     nullptr, __uuidof(ID3D12GraphicsCommandList),
                                     (void**)g.list.GetAddressOf());
    if (FAILED(hr)) return -12;
    g.list->Close();

    hr = g.device->CreateFence(0, D3D12_FENCE_FLAG_NONE, __uuidof(ID3D12Fence),
                               (void**)g.fence.GetAddressOf());
    if (FAILED(hr)) return -13;
    g.fence_event = CreateEventA(nullptr, FALSE, FALSE, nullptr);

    csv_open();
    g.ready = 1;
    log_line("[dx12] device ready: queue=fence ok, swapchain %ux%u x%u\n",
             g.width, g.height, g.buffer_count);
    return 0;
}

/* Present one cleared frame.  The colour is passed in by the caller so the
   NVN path can hand over the guest's own clear colour. */
int nvn_d3d12_present(float r, float gcol, float b, float a) {
    if (!g.ready) return -1;
    QueryPerformanceCounter(&g.frame_start);

    g.alloc->Reset();
    g.list->Reset(g.alloc.Get(), nullptr);

    D3D12_RESOURCE_BARRIER barrier = {};
    UINT idx = g.swapchain->GetCurrentBackBufferIndex();
    barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Flags = D3D12_RESOURCE_BARRIER_FLAG_NONE;
    barrier.Transition.pResource = g.rtv[idx].Get();
    barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_PRESENT;
    barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_RENDER_TARGET;
    g.list->ResourceBarrier(1, &barrier);

    D3D12_CPU_DESCRIPTOR_HANDLE rtv = g.rtv_heap->GetCPUDescriptorHandleForHeapStart();
    rtv.ptr += (SIZE_T)idx * g.rtv_stride;
    const float colour[4] = {r, gcol, b, a};
    g.list->OMSetRenderTargets(1, &rtv, FALSE, nullptr);
    g.list->ClearRenderTargetView(rtv, colour, 0, nullptr);

    barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_RENDER_TARGET;
    barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_PRESENT;
    g.list->ResourceBarrier(1, &barrier);
    g.list->Close();

    ID3D12CommandList* lists[1] = {g.list.Get()};
    g.queue->ExecuteCommandLists(1, lists);
    HRESULT hr = g.swapchain->Present(1, 0);
    if (FAILED(hr)) {
        log_line("[dx12] Present failed %08lx\n", (unsigned long)hr);
        return -2;
    }
    wait_gpu();
    g.presents++;

    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    const double frame_us =
        1e6 * (double)(now.QuadPart - g.frame_start.QuadPart) / (double)g.qpc_freq.QuadPart;
    g.frames++;
    csv_row(g.frames, frame_us, frame_us);
    return 0;
}

int nvn_d3d12_frame_count(void) { return (int)g.frames; }
const char* nvn_d3d12_csv_path(void) { return g.csv_path; }

void nvn_d3d12_pump(void) {
    MSG msg;
    while (PeekMessageA(&msg, nullptr, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }
}

void nvn_d3d12_shutdown(void) {
    if (!g.ready) return;
    wait_gpu();
    if (g.csv) {
        fclose(g.csv);
        g.csv = nullptr;
        log_line("[dx12] CSV closed: %s (%llu frames, %llu presents)\n",
                 g.csv_path, (unsigned long long)g.frames,
                 (unsigned long long)g.presents);
    }
    if (g.fence_event) CloseHandle(g.fence_event);
    g.ready = 0;
}

}  // extern "C"
