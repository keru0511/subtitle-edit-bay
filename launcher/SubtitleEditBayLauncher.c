#define UNICODE
#define _UNICODE

#include <windows.h>
#include <shellapi.h>
#include <wchar.h>

static void show_error(const wchar_t *message) {
    MessageBoxW(NULL, message, L"Subtitle Edit Bay - 起動エラー", MB_OK | MB_ICONERROR);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show_command) {
    (void)instance;
    (void)previous;
    (void)command_line;
    (void)show_command;

    const wchar_t *powershell_arguments = L"";
    int argument_count = 0;
    LPWSTR *arguments = CommandLineToArgvW(GetCommandLineW(), &argument_count);
    if (arguments == NULL || argument_count < 1) {
        show_error(L"起動オプションを解析できませんでした。");
        return 2;
    }
    if (argument_count > 1) {
        if (wcscmp(arguments[1], L"--setup") == 0) {
            powershell_arguments = L" -Action Setup";
            for (int index = 2; index < argument_count; index++) {
                const wchar_t *name = arguments[index];
                if (wcscmp(name, L"--migration-source") == 0 && index + 1 < argument_count) {
                    if (!SetEnvironmentVariableW(L"SUBTITLE_EDIT_BAY_MIGRATION_SOURCE", arguments[++index])) {
                        LocalFree(arguments);
                        show_error(L"移行元フォルダーをセットアップへ渡せませんでした。");
                        return 2;
                    }
                } else if (wcscmp(name, L"--skip-runtime-config") == 0) {
                    SetEnvironmentVariableW(L"SUBTITLE_EDIT_BAY_SKIP_RUNTIME_CONFIG", L"1");
                } else if (wcscmp(name, L"--skip-speaker-colors") == 0) {
                    SetEnvironmentVariableW(L"SUBTITLE_EDIT_BAY_SKIP_SPEAKER_COLORS", L"1");
                } else if (wcscmp(name, L"--skip-workspace-reference") == 0) {
                    SetEnvironmentVariableW(L"SUBTITLE_EDIT_BAY_SKIP_WORKSPACE_REFERENCE", L"1");
                } else {
                    LocalFree(arguments);
                    show_error(L"不明なセットアップオプションです。");
                    return 2;
                }
            }
        } else if (argument_count == 2 && wcscmp(arguments[1], L"--update") == 0) {
            powershell_arguments = L" -Action Update";
        } else if (argument_count == 2 && wcscmp(arguments[1], L"--probe-setup") == 0) {
            powershell_arguments = L" -ProbeSetupStateOnly";
        } else if (argument_count == 2 && wcscmp(arguments[1], L"--probe-setup-running") == 0) {
            powershell_arguments = L" -ProbeSetupRunningOnly";
        } else {
            LocalFree(arguments);
            show_error(L"不明な起動オプションです。");
            return 2;
        }
    }
    LocalFree(arguments);

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
