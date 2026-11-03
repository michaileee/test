#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR args, int show) {
    (void)instance; (void)previous; (void)args; (void)show;
    wchar_t base[32768], python[32768], script[32768], command[32768];
    DWORD count = GetModuleFileNameW(NULL, base, 32768);
    if (!count || count >= 15000) return 1;
    wchar_t *slash = wcsrchr(base, L'\\');
    if (!slash) return 1;
    *slash = 0;
    swprintf(python, 32768, L"%ls\\runtime\\pythonw.exe", base);
    swprintf(script, 32768, L"%ls\\app\\launch.py", base);
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES ||
        GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL, L"სრულად გაშალეთ ZIP და გაუშვით ProcurementRegistry.exe გაშლილი საქაღალდიდან.",
                    L"შესყიდვების რეესტრი", MB_OK | MB_ICONERROR);
        return 1;
    }
    swprintf(command, 32768, L"\"%ls\" -I \"%ls\"", python, script);
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    if (!CreateProcessW(python, command, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                        NULL, base, &startup, &process)) {
        MessageBoxW(NULL, L"აპლიკაცია ვერ გაეშვა. შეამოწმეთ, რომ ZIP სრულადაა გაშლილი და Windows 64-ბიტიანია.",
                    L"შესყიდვების რეესტრი", MB_OK | MB_ICONERROR);
        return 1;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
