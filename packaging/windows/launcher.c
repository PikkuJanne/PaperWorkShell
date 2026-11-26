/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Janne Vuorela.
 * Project-owned launcher; third-party rights remain with their owners.
 * No CRT, libgcc, shell command, ambient Python or user-content arguments.
 */
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0A00
#include <windows.h>
#include <shlobj.h>
#include <shellapi.h>

#define COUNT(a) (sizeof(a) / sizeof((a)[0]))
#define PATH_CAPACITY 32768
#define ENV_CAPACITY 131072
/* PE-format IMAGE_GUARD_SECURITY_COOKIE_UNUSED; absent in this MinGW header. */
#define PWS_IMAGE_GUARD_SECURITY_COOKIE_UNUSED 0x00000800

static const wchar_t application_mutex[] = L"Local\\io.github.pikkujanne.paperworkshell.App";
static const GUID folder_profile = {0x5e6c858f,0x0e22,0x4760,{0x9a,0xfe,0xea,0x33,0x17,0xb6,0x71,0x73}};
static const GUID folder_local = {0xf1b32785,0x6fba,0x4fcf,{0x9d,0x55,0x7b,0x8e,0x7f,0x15,0x70,0x91}};
static const GUID folder_roaming = {0x3eb685db,0x65f9,0x4cf6,{0xa0,0x3a,0xe3,0xef,0x65,0x72,0x9f,0x3d}};
static const GUID folder_windows = {0xf38bf404,0x1d43,0x42f2,{0x93,0x05,0x67,0xde,0x0b,0x28,0xfc,0x23}};
static const GUID folder_system = {0x1ac14e77,0x02e7,0x4e5d,{0xb7,0x44,0x2e,0xb1,0xae,0x51,0x98,0xb7}};
static wchar_t runtime_directory[PATH_CAPACITY];
static wchar_t control_path[PATH_CAPACITY];
static wchar_t python_executable[PATH_CAPACITY];
static wchar_t command_line[PATH_CAPACITY];
static wchar_t temporary_directory[PATH_CAPACITY];
static wchar_t system_windows_directory[PATH_CAPACITY];
static wchar_t child_environment[ENV_CAPACITY];
static SIZE_T environment_length;
void WINAPI pws_entry(void);
/* Windows requires this loader cookie for subsystem 6.3 and later, including
 * CRT-free programs. The compiler does not instrument /GS checks here, so mark
 * the cookie UNUSED rather than advertising protection we do not implement.
 * The pointer also provides a genuine image relocation for ASLR. */
static ULONG_PTR loader_cookie = 0x00002b992ddfa232ULL;
const IMAGE_LOAD_CONFIG_DIRECTORY64 _load_config_used __attribute__((used)) = {
    .Size = sizeof(IMAGE_LOAD_CONFIG_DIRECTORY64),
    .SecurityCookie = (ULONGLONG)(ULONG_PTR)&loader_cookie,
    .GuardFlags = PWS_IMAGE_GUARD_SECURITY_COOKIE_UNUSED
};

static void clear_bytes(void *target, SIZE_T length) {
    volatile unsigned char *cursor = (volatile unsigned char *)target;
    while (length--) *cursor++ = 0;
}

static BOOL append(wchar_t *target, SIZE_T *used, SIZE_T capacity, const wchar_t *value) {
    while (*value) {
        if (*used + 1 >= capacity) return FALSE;
        target[(*used)++] = *value++;
    }
    target[*used] = L'\0';
    return TRUE;
}

static BOOL equal(const wchar_t *left, const wchar_t *right) {
    while (*left && *left == *right) { ++left; ++right; }
    return *left == *right;
}

static void fail(const wchar_t *message) {
    MessageBoxW(NULL, message, L"PaperWorkShell", MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
    ExitProcess(2);
}

static BOOL environment_pair(const wchar_t *name, const wchar_t *value) {
    if (!append(child_environment, &environment_length, ENV_CAPACITY, name) ||
        !append(child_environment, &environment_length, ENV_CAPACITY, L"=") ||
        !append(child_environment, &environment_length, ENV_CAPACITY, value) ||
        environment_length + 2 >= ENV_CAPACITY) return FALSE;
    child_environment[environment_length++] = L'\0';
    child_environment[environment_length] = L'\0';
    return TRUE;
}

static BOOL known_environment_pair(const wchar_t *name, const GUID *folder) {
    PWSTR value = NULL;
    BOOL result;
    if (FAILED(SHGetKnownFolderPath(folder, 0, NULL, &value)) || !value) return FALSE;
    result = environment_pair(name, value);
    CoTaskMemFree(value);
    return result;
}

static BOOL update_lease_environment(HANDLE lease) {
    static const wchar_t digits[] = L"0123456789abcdef";
    wchar_t value[1 + sizeof(ULONG_PTR) * 2];
    ULONG_PTR number = (ULONG_PTR)lease;
    SIZE_T i = COUNT(value) - 1;
    value[i] = L'\0';
    do {
        value[--i] = digits[number & 15];
        number >>= 4;
    } while (number);
    return environment_pair(L"PWS_UPDATE_LEASE", value + i);
}

static BOOL prepare_environment(BOOL update, HANDLE lease) {
    PWSTR local = NULL;
    wchar_t system_drive[3];
    SIZE_T used = 0;
    DWORD attributes, windows_length;
    /* Derive the system volume from the native shared Windows directory,
     * never from ambient SystemDrive. Known-folder expansion needs this key. */
    windows_length = GetSystemWindowsDirectoryW(system_windows_directory, COUNT(system_windows_directory));
    if (windows_length < 2 || windows_length >= COUNT(system_windows_directory) ||
        !((system_windows_directory[0] >= L'A' && system_windows_directory[0] <= L'Z') ||
          (system_windows_directory[0] >= L'a' && system_windows_directory[0] <= L'z')) ||
        system_windows_directory[1] != L':' ||
        (windows_length > 2 && system_windows_directory[2] != L'\\')) return FALSE;
    system_drive[0] = system_windows_directory[0];
    system_drive[1] = L':';
    system_drive[2] = L'\0';
    if (FAILED(SHGetKnownFolderPath(&folder_local, 0, NULL, &local)) || !local) return FALSE;
    if (!append(temporary_directory, &used, COUNT(temporary_directory), local) ||
        !append(temporary_directory, &used, COUNT(temporary_directory), L"\\Temp")) {
        CoTaskMemFree(local);
        return FALSE;
    }
    CoTaskMemFree(local);
    attributes = GetFileAttributesW(temporary_directory);
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        if (!CreateDirectoryW(temporary_directory, NULL)) return FALSE;
        attributes = GetFileAttributesW(temporary_directory);
    }
    if (attributes == INVALID_FILE_ATTRIBUTES || !(attributes & FILE_ATTRIBUTE_DIRECTORY)) return FALSE;
    /* Windows requires a sorted, double-NUL-terminated Unicode environment.
     * No ambient PWS_*, PYTHON*, QT_*, PATH or command text is copied. */
    return known_environment_pair(L"APPDATA", &folder_roaming) &&
        known_environment_pair(L"LOCALAPPDATA", &folder_local) &&
        known_environment_pair(L"PATH", &folder_system) &&
        (!update || update_lease_environment(lease)) &&
        environment_pair(L"SystemDrive", system_drive) &&
        known_environment_pair(L"SystemRoot", &folder_windows) &&
        environment_pair(L"TEMP", temporary_directory) &&
        environment_pair(L"TMP", temporary_directory) &&
        known_environment_pair(L"USERPROFILE", &folder_profile);
}

static BOOL directory_is_plain(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    return attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY) &&
        !(attributes & FILE_ATTRIBUTE_REPARSE_POINT);
}

static HANDLE open_installation_lease(BOOL *profile_available) {
    PWSTR local = NULL;
    wchar_t drive[4] = {0, L':', L'\\', 0};
    SIZE_T used = 0;
    HANDLE lease = INVALID_HANDLE_VALUE;
    BY_HANDLE_FILE_INFORMATION info;
    *profile_available = FALSE;
    if (FAILED(SHGetKnownFolderPath(&folder_local, 0, NULL, &local)) || !local) return lease;
    if (!((local[0] >= L'A' && local[0] <= L'Z') ||
          (local[0] >= L'a' && local[0] <= L'z')) || local[1] != L':' || local[2] != L'\\') {
        CoTaskMemFree(local);
        return lease;
    }
    drive[0] = local[0];
    if (GetDriveTypeW(drive) != DRIVE_FIXED || !directory_is_plain(local)) {
        CoTaskMemFree(local);
        return lease;
    }
    *profile_available = TRUE;
    if (append(control_path, &used, COUNT(control_path), local) &&
        append(control_path, &used, COUNT(control_path), L"\\PaperWorkShell.lifecycle-v1.lock"))
        lease = CreateFileW(control_path, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_ALWAYS,
                            FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    CoTaskMemFree(local);
    if (lease != INVALID_HANDLE_VALUE &&
        (!GetFileInformationByHandle(lease, &info) || info.nNumberOfLinks != 1 ||
         (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)))) {
        CloseHandle(lease);
        return INVALID_HANDLE_VALUE;
    }
    return lease;
}

static BOOL select_runtime(SIZE_T runtime_length) {
    SIZE_T root_length = runtime_length, used = 0;
    DWORD read = 0, error;
    char selector[2];
    wchar_t slot[2] = {0, 0};
    HANDLE state;
    BY_HANDLE_FILE_INFORMATION info;
    BOOL valid;
    while (root_length && runtime_directory[root_length - 1] != L'\\') --root_length;
    if (root_length <= 3 || !equal(runtime_directory + root_length, L"runtime")) return TRUE;
    runtime_directory[root_length - 1] = L'\0';
    if (!directory_is_plain(runtime_directory) ||
        !append(control_path, &used, COUNT(control_path), runtime_directory) ||
        !append(control_path, &used, COUNT(control_path), L"\\update-state-v1")) return FALSE;
    state = CreateFileW(control_path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                       FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (state == INVALID_HANDLE_VALUE) {
        error = GetLastError();
        if (error != ERROR_FILE_NOT_FOUND) return FALSE;
        selector[0] = '0';
    } else {
        valid = GetFileInformationByHandle(state, &info) && info.nNumberOfLinks == 1 &&
            !(info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) &&
            info.nFileSizeHigh == 0 && info.nFileSizeLow <= 16384 &&
            ReadFile(state, selector, sizeof(selector), &read, NULL) &&
            read == sizeof(selector) && selector[1] == '\n';
        CloseHandle(state);
        if (!valid) return FALSE;
    }
    used = root_length - 1;
    if (selector[0] == '0')
        return append(runtime_directory, &used, COUNT(runtime_directory), L"\\runtime");
    if (selector[0] < 'A' || selector[0] > 'C') return FALSE;
    slot[0] = (wchar_t)selector[0];
    return append(runtime_directory, &used, COUNT(runtime_directory), L"\\update-slot-") &&
        append(runtime_directory, &used, COUNT(runtime_directory), slot) &&
        directory_is_plain(runtime_directory) &&
        append(runtime_directory, &used, COUNT(runtime_directory), L"\\runtime") &&
        directory_is_plain(runtime_directory);
}

static HANDLE pin_program(void) {
    BY_HANDLE_FILE_INFORMATION info;
    HANDLE file = CreateFileW(python_executable, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                              FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (file != INVALID_HANDLE_VALUE &&
        (!GetFileInformationByHandle(file, &info) || info.nNumberOfLinks != 1 ||
         (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)))) {
        CloseHandle(file);
        return INVALID_HANDLE_VALUE;
    }
    return file;
}

static BOOL prepare_program(BOOL review, BOOL notices, BOOL update) {
    DWORD count = GetModuleFileNameW(NULL, runtime_directory, COUNT(runtime_directory));
    SIZE_T directory_length, used = 0;
    if (!count || count >= COUNT(runtime_directory)) return FALSE;
    directory_length = count;
    while (directory_length && runtime_directory[directory_length - 1] != L'\\') --directory_length;
    if (directory_length <= 3) return FALSE;
    runtime_directory[directory_length - 1] = L'\0';
    if (!review && !update && !select_runtime(directory_length - 1)) return FALSE;
    if (!append(python_executable, &used, COUNT(python_executable), runtime_directory) ||
        !append(python_executable, &used, COUNT(python_executable), L"\\PaperWorkShellDesktop.exe")) return FALSE;
    used = 0;
    /* A Windows filename cannot contain a quote. The fixed executable ends in
     * .exe, so no trailing backslash can escape the closing quote. */
    return append(command_line, &used, COUNT(command_line), L"\"") &&
        append(command_line, &used, COUNT(command_line), python_executable) &&
        append(command_line, &used, COUNT(command_line), L"\" -I -B -m ") &&
        append(command_line, &used, COUNT(command_line), review ?
            L"paperworkshell.ui.uninstall_review" : update ? L"paperworkshell.ui.update_dialog" : notices ?
            L"paperworkshell.ui.notices" : L"paperworkshell");
}

static BOOL start_child(BOOL update, HANDLE lease, PROCESS_INFORMATION *process) {
    STARTUPINFOEXW startup;
    SIZE_T attribute_size = 0;
    DWORD flags = CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED;
    BOOL started, attributes_ready = FALSE;
    clear_bytes(&startup, sizeof(startup));
    startup.StartupInfo.cb = sizeof(startup.StartupInfo);
    if (update) {
        InitializeProcThreadAttributeList(NULL, 1, 0, &attribute_size);
        if (!attribute_size || !SetHandleInformation(lease, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT))
            return FALSE;
        startup.lpAttributeList = HeapAlloc(GetProcessHeap(), 0, attribute_size);
        if (!startup.lpAttributeList) return FALSE;
        attributes_ready = InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &attribute_size);
        if (!attributes_ready ||
            !UpdateProcThreadAttribute(startup.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                       &lease, sizeof(lease), NULL, NULL)) {
            if (attributes_ready) DeleteProcThreadAttributeList(startup.lpAttributeList);
            HeapFree(GetProcessHeap(), 0, startup.lpAttributeList);
            return FALSE;
        }
        startup.StartupInfo.cb = sizeof(startup);
        flags |= EXTENDED_STARTUPINFO_PRESENT;
    }
    started = CreateProcessW(python_executable, command_line, NULL, NULL, update, flags,
                             child_environment, runtime_directory, &startup.StartupInfo, process);
    if (attributes_ready) {
        DeleteProcThreadAttributeList(startup.lpAttributeList);
        HeapFree(GetProcessHeap(), 0, startup.lpAttributeList);
    }
    return started;
}

void WINAPI pws_entry(void) {
    int argument_count = 0;
    LPWSTR *arguments;
    BOOL review = FALSE, notices = FALSE, update = FALSE, profile_available;
    HANDLE mutex = NULL, job = NULL, lease = INVALID_HANDLE_VALUE, program = INVALID_HANDLE_VALUE;
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits;
    PROCESS_INFORMATION process;
    DWORD outcome = 2;
    /* Process-local only; the child inherits this mode. Do not change registry,
     * user policy, Windows security settings or global crash-reporting state. */
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    if (!SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32) || !SetDllDirectoryW(L""))
        fail(L"[LAUNCHER_INITIALIZATION_FAILED]\nPaperWorkShell could not prepare a safe launch. Repair the installation and try again.");
    arguments = CommandLineToArgvW(GetCommandLineW(), &argument_count);
    if (!arguments) fail(L"[LAUNCHER_ARGUMENT_INVALID]\nOpen PaperWorkShell from its normal application shortcut.");
    if (argument_count == 2 && equal(arguments[1], L"--review-uninstall")) review = TRUE;
    else if (argument_count == 2 && equal(arguments[1], L"--notices")) notices = TRUE;
    else if (argument_count == 2 && equal(arguments[1], L"--update")) update = TRUE;
    else if (argument_count != 1) {
        LocalFree(arguments);
        fail(L"[LAUNCHER_ARGUMENT_INVALID]\nThis launcher does not accept files or options. Open PaperWorkShell normally and use its visible import controls.");
    }
    LocalFree(arguments);
    /* Both fixed auxiliary modules own their existing application guard. */
    if (!review && !notices && !update) {
        SetLastError(0);
        mutex = CreateMutexW(NULL, TRUE, application_mutex);
        if (!mutex || GetLastError() == ERROR_ALREADY_EXISTS) {
            if (mutex) CloseHandle(mutex);
            fail(L"[LAUNCHER_APPLICATION_BUSY]\nPaperWorkShell or its removal review is already open. Close it before starting another copy.");
        }
    }
    lease = open_installation_lease(&profile_available);
    if (!profile_available)
        fail(L"[LAUNCHER_PROFILE_UNAVAILABLE]\nPaperWorkShell could not use Windows' local folders. Start it from its normal application shortcut, or sign out and sign in before trying again. No case data was changed.");
    if (lease == INVALID_HANDLE_VALUE)
        fail(L"[LAUNCHER_APPLICATION_BUSY]\nClose PaperWorkShell, its updates window or setup in other sessions and try again.");
    if (!prepare_program(review, notices, update)) {
        if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
        fail(L"[LAUNCHER_RUNTIME_UNAVAILABLE]\nPaperWorkShell could not find its installed runtime. Repair the installation and try again. Your case data remains separate.");
    }
    program = pin_program();
    if (program == INVALID_HANDLE_VALUE)
        fail(L"[LAUNCHER_RUNTIME_UNAVAILABLE]\nPaperWorkShell could not verify its installed runtime. Open PaperWorkShell Updates to review recovery options.");
    if (!prepare_environment(update, lease)) {
        if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
        fail(L"[LAUNCHER_PROFILE_UNAVAILABLE]\nPaperWorkShell could not use Windows' local folders. Start it from its normal application shortcut, or sign out and sign in before trying again. No case data was changed.");
    }
    clear_bytes(&limits, sizeof(limits));
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    job = CreateJobObjectW(NULL, NULL);
    if (!job || !SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) {
        if (job) CloseHandle(job);
        if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
        fail(L"[LAUNCHER_INITIALIZATION_FAILED]\nPaperWorkShell could not prepare its application process. Close the application and try again.");
    }
    clear_bytes(&process, sizeof(process));
    if (!start_child(update, lease, &process)) {
        CloseHandle(job);
        if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
        fail(L"[LAUNCHER_PROCESS_FAILED]\nPaperWorkShell could not start its installed runtime. Repair the installation and try again.");
    }
    CloseHandle(program);
    if (!AssignProcessToJobObject(job, process.hProcess) || ResumeThread(process.hThread) == (DWORD)-1) {
        TerminateProcess(process.hProcess, 2);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        CloseHandle(job);
        if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
        fail(L"[LAUNCHER_PROCESS_FAILED]\nPaperWorkShell could not start safely. Close the application and try again.");
    }
    CloseHandle(process.hThread);
    if (WaitForSingleObject(process.hProcess, INFINITE) != WAIT_OBJECT_0 ||
        !GetExitCodeProcess(process.hProcess, &outcome)) outcome = 2;
    CloseHandle(process.hProcess);
    CloseHandle(job);
    CloseHandle(lease);
    if (mutex) { ReleaseMutex(mutex); CloseHandle(mutex); }
    if (review && outcome > 1)
        fail(L"[LAUNCHER_REVIEW_FAILED]\nRemoval review could not finish. Close PaperWorkShell, keep the remaining case data and try again.");
    if (notices && outcome != 0)
        fail(L"[LAUNCHER_NOTICES_FAILED]\nPaperWorkShell notices could not finish. Close the notices window and try again, or repair the installation.");
    if (update && outcome != 0)
        fail(L"[LAUNCHER_UPDATE_FAILED]\nPaperWorkShell Updates could not finish. Open Updates again to inspect the available recovery options.");
    if (!review && !notices && !update && outcome != 0)
        fail(L"[LAUNCHER_APPLICATION_FAILED]\nPaperWorkShell closed before finishing. Start it again or open PaperWorkShell Updates to review recovery options.");
    ExitProcess(outcome);
}
