#define UNICODE
#define _UNICODE

#include <windows.h>
#include <wchar.h>

static void show_error(const wchar_t *message) {
    MessageBoxW(NULL, message, L"Subtitle Edit Bay - 起動エラー", MB_OK | MB_ICONERROR);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show_command) {
    (void)instance;
    (void)previous;
    (void)command_line;
    (void)show_command;

    wchar_t module_path[32768];
    DWORD length = GetModuleFileNameW(NULL, module_path, (DWORD)(sizeof(module_path) / sizeof(module_path[0])));
    if (length == 0 || length >= (DWORD)(sizeof(module_path) / sizeof(module_path[0]))) {
        show_error(L"ランチャー自身の場所を解決できませんでした。");
        return 2;
    }

    wchar_t *separator = wcsrchr(module_path, L'\\');
    if (separator == NULL) {
        show_error(L"インストール先を解決できませんでした。");
        return 2;
    }
    *separator = L'\0';

    wchar_t script_path[32768];
    if (swprintf_s(script_path, sizeof(script_path) / sizeof(script_path[0]), L"%s\\scripts\\launch.ps1", module_path) < 0) {
        show_error(L"起動スクリプトのパスが長すぎます。");
        return 2;
    }

    wchar_t system_directory[MAX_PATH];
    UINT system_length = GetSystemDirectoryW(system_directory, (UINT)(sizeof(system_directory) / sizeof(system_directory[0])));
    if (system_length == 0 || system_length >= (UINT)(sizeof(system_directory) / sizeof(system_directory[0]))) {
        show_error(L"Windows PowerShellの場所を解決できませんでした。");
        return 2;
    }

    wchar_t command[65536];
    if (swprintf_s(
            command,
            sizeof(command) / sizeof(command[0]),
            L"\"%s\\WindowsPowerShell\\v1.0\\powershell.exe\" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"%s\"",
            system_directory,
            script_path) < 0) {
        show_error(L"起動コマンドが長すぎます。");
        return 2;
    }

    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    startup.dwFlags |= STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_HIDE;
    if (!CreateProcessW(
            NULL,
            command,
            NULL,
            NULL,
            FALSE,
            CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
            NULL,
            module_path,
            &startup,
            &process)) {
        show_error(L"起動スクリプトを実行できませんでした。初回セットアップを確認してください。");
        return (int)GetLastError();
    }

    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exit_code = 1;
    GetExitCodeProcess(process.hProcess, &exit_code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)exit_code;
}
