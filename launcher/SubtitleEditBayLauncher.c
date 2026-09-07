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
    (void)show_command;

    const wchar_t *powershell_arguments = L"";
    if (command_line != NULL && command_line[0] != L'\0') {
        if (wcscmp(command_line, L"--setup") == 0) {
            powershell_arguments = L" -Action Setup";
        } else if (wcscmp(command_line, L"--update") == 0) {
            powershell_arguments = L" -Action Update";
        } else if (wcscmp(command_line, L"--probe-setup") == 0) {
            powershell_arguments = L" -ProbeSetupStateOnly";
        } else {
            show_error(L"不明な起動オプションです。");
            return 2;
        }
    }

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
            L"\"%s\\WindowsPowerShell\\v1.0\\powershell.exe\" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"%s\"%s",
            system_directory,
            script_path,
            powershell_arguments) < 0) {
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
