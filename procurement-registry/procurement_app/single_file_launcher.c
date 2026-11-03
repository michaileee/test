/* Native Windows single-file loader. Uses only Windows APIs and the C runtime. */
#ifndef UNICODE
#define UNICODE 1
#endif
#include <windows.h>
#include <bcrypt.h>
#include <fdi.h>
#include <shlobj.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include "payload_manifest.h"

extern const unsigned char _binary_payload_cab_start[];
extern const unsigned char _binary_payload_cab_end[];
static wchar_t destination[32768], user_root[32768];
static int quiet = 0;
static BCRYPT_ALG_HANDLE algorithm = NULL;
static const wchar_t *stage = L"startup";
static wchar_t component[1024];
static int cabinet_error = 0, cabinet_detail = 0;
static DWORD child_error = 0;

typedef struct { int memory; size_t position; HANDLE file; } IO;
static FNALLOC(mem_alloc) { return HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, cb); }
static FNFREE(mem_free) { if (pv) HeapFree(GetProcessHeap(), 0, pv); }
static FNOPEN(cab_open) {
    (void)pszFile; (void)oflag; (void)pmode;
    IO *io = mem_alloc(sizeof(IO));
    if (!io) return -1;
    io->memory = 1;
    return (INT_PTR)io;
}
static FNREAD(cab_read) {
    IO *io = (IO *)hf;
    if (io->memory) {
        size_t size = (size_t)(_binary_payload_cab_end - _binary_payload_cab_start);
        if (io->position > size) return (UINT)-1;
        size_t left = size - io->position;
        if (cb > left) cb = (UINT)left;
        memcpy(pv, _binary_payload_cab_start + io->position, cb);
        io->position += cb;
        return cb;
    }
    DWORD got = 0;
    return ReadFile(io->file, pv, cb, &got, NULL) ? got : (UINT)-1;
}
static FNWRITE(cab_write) {
    IO *io = (IO *)hf;
    DWORD written = 0;
    return WriteFile(io->file, pv, cb, &written, NULL) ? written : (UINT)-1;
}
static FNCLOSE(cab_close) {
    IO *io = (IO *)hf;
    int result = io->memory || CloseHandle(io->file);
    mem_free(io);
    return result ? 0 : -1;
}
static FNSEEK(cab_seek) {
    IO *io = (IO *)hf;
    if (!io->memory) return -1;
    LONGLONG size = _binary_payload_cab_end - _binary_payload_cab_start;
    LONGLONG next = (seektype == SEEK_SET ? 0 : seektype == SEEK_CUR ? (LONGLONG)io->position : size) + dist;
    if (next < 0 || next > size) return -1;
    io->position = (size_t)next;
    return (__LONG32)next;
}
static int directories(const wchar_t *path) {
    int result = SHCreateDirectoryExW(NULL, path, NULL);
    return result == ERROR_SUCCESS || result == ERROR_ALREADY_EXISTS || result == ERROR_FILE_EXISTS;
}
static int hash_matches(const wchar_t *path, const unsigned char *expected) {
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              NULL, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    BCRYPT_HASH_HANDLE hash = NULL;
    unsigned char buffer[65536], value[32];
    DWORD got = 0;
    int valid = BCryptCreateHash(algorithm, &hash, NULL, 0, NULL, 0, 0) >= 0;
    while (valid) {
        if (!ReadFile(file, buffer, sizeof(buffer), &got, NULL)) { valid = 0; break; }
        if (!got) break;
        valid = BCryptHashData(hash, buffer, got, 0) >= 0;
    }
    if (valid) valid = BCryptFinishHash(hash, value, sizeof(value), 0) >= 0 && !memcmp(value, expected, 32);
    if (hash) BCryptDestroyHash(hash);
    CloseHandle(file);
    return valid;
}
static int cache_valid(void) {
    wchar_t path[32768];
    for (size_t i = 0; i < EXPECTED_COUNT; i++) {
        wcsncpy(component, expected_files[i].name, 1023);
        if (swprintf(path, 32768, L"%ls\\%ls", destination, expected_files[i].name) < 0 ||
            !hash_matches(path, expected_files[i].hash)) return 0;
    }
    return 1;
}
static int known_member(const wchar_t *name) {
    for (size_t i = 0; i < EXPECTED_COUNT; i++)
        if (!wcscmp(name, expected_files[i].name)) return 1;
    return 0;
}
static FNFDINOTIFY(extract_notice) {
    if (fdint == fdintCOPY_FILE) {
        wchar_t name[1024], path[32768], parent[32768];
        if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, pfdin->psz1, -1, name, 1024) ||
            !known_member(name)) return -1;
        wcsncpy(component, name, 1023);
        if (swprintf(path, 32768, L"%ls\\%ls", destination, name) < 0) return -1;
        wcscpy(parent, path);
        wchar_t *slash = wcsrchr(parent, L'\\');
        if (!slash) return -1;
        *slash = 0;
        if (!directories(parent)) return -1;
        IO *io = mem_alloc(sizeof(IO));
        if (!io) return -1;
        io->file = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (io->file == INVALID_HANDLE_VALUE) { mem_free(io); return -1; }
        return (INT_PTR)io;
    }
    if (fdint == fdintCLOSE_FILE_INFO) {
        return cab_close(pfdin->hf) == 0;
    }
    if (fdint == fdintNEXT_CABINET || fdint == fdintPARTIAL_FILE) return -1;
    return 0;
}
static int fail(const wchar_t *message) {
    DWORD windows_error = GetLastError();
    if (user_root[0]) {
        wchar_t path[32768];
        swprintf(path, 32768, L"%ls\\bootstrap_error.log", user_root);
        HANDLE file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (file != INVALID_HANDLE_VALUE) {
            DWORD written;
            wchar_t detail[4096];
            swprintf(detail, 4096, L"%ls\nstage=%ls\nwin32_error=%lu\ncabinet_error=%d/%d\nchild_exit=%lu\ncomponent=%ls\n",
                     message, stage, windows_error, cabinet_error, cabinet_detail, child_error, component);
            char text[16384];
            int length = WideCharToMultiByte(CP_UTF8, 0, detail, -1, text, sizeof(text), NULL, NULL);
            if (length > 1) WriteFile(file, text, (DWORD)length-1, &written, NULL);
            CloseHandle(file);
        }
    }
    if (!quiet) MessageBoxW(NULL, message, L"შესყიდვების რეესტრი", MB_OK | MB_ICONERROR);
    return 1;
}
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR args, int show) {
    (void)instance; (void)previous; (void)show;
    quiet = !wcscmp(args, L"--self-test");
    wchar_t local[32768], mutex_name[128];
    DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", local, 32768);
    if (!length || length > 12000) return fail(L"მომხმარებლის საქაღალდე ვერ მოიძებნა.");
    swprintf(user_root, 32768, L"%ls\\ProcurementRegistry", local);
    swprintf(destination, 32768, L"%ls\\cache\\%ls", user_root, BUILD_ID);
    if (!directories(destination)) return fail(L"აპლიკაციის ფაილების მომზადება ვერ მოხერხდა.");
    swprintf(mutex_name, 128, L"Local\\ProcurementRegistry_Cache_%ls", BUILD_ID);
    HANDLE mutex = CreateMutexW(NULL, FALSE, mutex_name);
    if (!mutex) return fail(L"აპლიკაციის მომზადების კოორდინაცია ვერ მოხერხდა.");
    DWORD wait = WaitForSingleObject(mutex, 120000);
    if (wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED) {
        CloseHandle(mutex);
        return fail(L"სხვა გაშვების მომზადება ჯერ მიმდინარეობს. ცოტა ხანში ხელახლა სცადეთ.");
    }
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0) < 0) {
        ReleaseMutex(mutex); CloseHandle(mutex);
        return fail(L"ფაილების მთლიანობის შემოწმება ვერ ჩაირთო.");
    }
    stage = L"verify-cache";
    int valid = cache_valid();
    if (!valid) {
        stage = L"extract-cabinet";
        ERF error = {0};
        HFDI cabinet = FDICreate(mem_alloc, mem_free, cab_open, cab_read, cab_write, cab_close, cab_seek, cpuUNKNOWN, &error);
        valid = cabinet && FDICopy(cabinet, "payload.cab", "", 0, extract_notice, NULL, NULL);
        cabinet_error = error.erfOper;
        cabinet_detail = error.erfType;
        if (cabinet) FDIDestroy(cabinet);
        if (valid) { stage = L"verify-extracted-files"; valid = cache_valid(); }
    }
    BCryptCloseAlgorithmProvider(algorithm, 0);
    ReleaseMutex(mutex); CloseHandle(mutex);
    if (!valid) return fail(L"ფაილების აღდგენა ვერ დასრულდა. დახურეთ აპლიკაციის სხვა გაშვებები და ხელახლა სცადეთ.");
    wchar_t python[32768], script[32768], command[32768];
    swprintf(python, 32768, L"%ls\\runtime\\%ls", destination, quiet ? L"python.exe" : L"pythonw.exe");
    swprintf(script, 32768, L"%ls\\app\\%ls", destination, quiet ? L"diagnostics.py" : L"launch.py");
    if (swprintf(command, 32768, L"\"%ls\" -I -B \"%ls\"", python, script) < 0)
        return fail(L"აპლიკაციის მისამართი ზედმეტად გრძელია.");
    STARTUPINFOW startup = {0}; PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    stage = L"start-python";
    if (!CreateProcessW(python, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, destination, &startup, &process))
        return fail(L"Python-ის ჩაშენებული გარემო ვერ გაეშვა. შეამოწმეთ აპლიკაციის უსაფრთხოების შეტყობინება.");
    CloseHandle(process.hThread);
    DWORD result = 0;
    if (quiet) {
        if (WaitForSingleObject(process.hProcess, 120000) != WAIT_OBJECT_0) result = 2;
        else GetExitCodeProcess(process.hProcess, &result);
    } else if (WaitForSingleObject(process.hProcess, 2000) == WAIT_OBJECT_0) {
        GetExitCodeProcess(process.hProcess, &child_error);
        if (child_error) result = fail(L"აპლიკაციის გაშვება შეწყდა. დამატებითი ინფორმაცია ჩაიწერა შეცდომის ანგარიშში.");
    }
    CloseHandle(process.hProcess);
    return (int)result;
}
