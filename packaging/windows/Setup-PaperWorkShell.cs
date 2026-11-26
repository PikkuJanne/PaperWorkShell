// SPDX-License-Identifier: MIT
// Windows 11 x64 setup for the prepared, app-local PaperWorkShell engineering package.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Management;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Automation;
using Microsoft.Win32;

internal static class PaperWorkShellSetup
{
    private const string Marker = "runtime/paperworkshell-installed-v1.json";
    private const string Launcher = "runtime/PaperWorkShell.exe";
    private const string Desktop = "runtime/PaperWorkShellDesktop.exe";
    private const string Terms = "notices/Microsoft-runtime-recipient-terms.txt";
    private const string Registration = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}_is1";
    private const int MetadataLimit = 16 * 1024 * 1024;
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer
        { MaxJsonLength = MetadataLimit, RecursionLimit = 48 };
    private static readonly Regex DigestPattern = new Regex(@"\A[0-9a-fA-F]{64}\z");

    private sealed class SetupError : Exception
    {
        internal readonly string Code;
        internal SetupError(string code, string next) : base(next) { Code = code; }
    }

    private sealed class Artifact : IDisposable
    {
        internal FileStream Installer;
        internal string Candidate, InstallerPath, InstallerHash, InventoryHash, TermsHash;
        internal readonly Dictionary<string, Dictionary<string, object>> Files =
            new Dictionary<string, Dictionary<string, object>>(StringComparer.OrdinalIgnoreCase);

        public void Dispose() { if (Installer != null) Installer.Dispose(); }
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool IsWow64Process2(IntPtr process, out ushort processMachine, out ushort nativeMachine);

    private static void Require(bool condition, string code, string next)
    {
        if (!condition) throw new SetupError(code, next);
    }

    private static bool Same(string left, string right)
    {
        return String.Equals(left, right, StringComparison.OrdinalIgnoreCase);
    }

    private static Dictionary<string, object> Object(object value)
    {
        var result = value as Dictionary<string, object>;
        if (result == null) throw new FormatException();
        return result;
    }

    private static string Field(Dictionary<string, object> row, string key)
    {
        var result = row[key] as string;
        if (result == null) throw new FormatException();
        return result;
    }

    private static long Number(Dictionary<string, object> row, string key)
    {
        object value = row[key];
        if (!(value is int) && !(value is long)) throw new FormatException();
        return Convert.ToInt64(value, CultureInfo.InvariantCulture);
    }

    private static string Hash(Stream source)
    {
        using (var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(source)).Replace("-", "").ToLowerInvariant();
    }

    // Limit inspection to ordinary local files. Inno still enforces its own destination and link checks.
    private static string LocalPath(string path)
    {
        string full = Path.GetFullPath(path);
        if (!Regex.IsMatch(full, @"\A[A-Za-z]:\\") || (full.Length > 3 && full.IndexOf(':', 2) >= 0) ||
            new DriveInfo(Path.GetPathRoot(full)).DriveType != DriveType.Fixed)
            throw new IOException("Use paths on a local fixed Windows drive.");
        for (string item = full; item != null; item = Path.GetDirectoryName(item))
        {
            try
            {
                if ((File.GetAttributes(item) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("A file or parent folder is redirected.");
            }
            catch (FileNotFoundException) { }
            catch (DirectoryNotFoundException) { }
        }
        return full;
    }

    private static Dictionary<string, object> ReadBoundJson(string path, string expected)
    {
        using (var input = new FileStream(LocalPath(path), FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            if (input.Length <= 0 || input.Length > MetadataLimit || !Same(Hash(input), expected))
                throw new FormatException();
            input.Position = 0;
            using (var reader = new StreamReader(input, new UTF8Encoding(false, true)))
                return Object(Json.DeserializeObject(reader.ReadToEnd()));
        }
    }

    private static bool SafeMember(string path)
    {
        string[] parts = path.Split('/');
        if (parts.Length < 2 || Array.IndexOf(new[] { "runtime", "notices", "sources", "evidence" }, parts[0]) < 0)
            return false;
        foreach (string part in parts)
            if (part.Length == 0 || part.EndsWith(".", StringComparison.Ordinal) ||
                part.EndsWith(" ", StringComparison.Ordinal) || part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
                Regex.IsMatch(part, @"\A(?:CON|PRN|AUX|NUL|COM[0-9\u00b9\u00b2\u00b3]|LPT[0-9\u00b9\u00b2\u00b3])(?:\.|\z)", RegexOptions.IgnoreCase))
                return false;
        return true;
    }

    private static void OpenArtifact(Artifact artifact, string installerPath, string receiptPath, string receiptHash)
    {
        try
        {
            var receipt = ReadBoundJson(receiptPath, receiptHash);
            artifact.Candidate = Field(receipt, "candidate_id");
            artifact.InstallerHash = Field(receipt, "installer_sha256");
            artifact.InventoryHash = Field(receipt, "inventory_sha256");
            artifact.TermsHash = Field(receipt, "microsoft_recipient_terms_sha256");
            artifact.InstallerPath = LocalPath(installerPath);
            if (Number(receipt, "schema_version") != 1 ||
                !Regex.IsMatch(artifact.Candidate, @"\A[a-z0-9-]{3,55}\z") ||
                Field(receipt, "status") != "unsigned-internal-built" ||
                !false.Equals(receipt["public_distribution_allowed"]) ||
                !false.Equals(receipt["public_trusted_signature"]) ||
                Field(receipt, "microsoft_runtime_delivery") != "app-local-community2022" ||
                !DigestPattern.IsMatch(artifact.InstallerHash) || !DigestPattern.IsMatch(artifact.InventoryHash) ||
                !DigestPattern.IsMatch(artifact.TermsHash) ||
                !Same(Path.GetFileName(artifact.InstallerPath), Field(receipt, "installer")) ||
                !Same(Path.GetExtension(artifact.InstallerPath), ".exe"))
                throw new FormatException();

            var inventory = ReadBoundJson(Path.Combine(Path.GetDirectoryName(LocalPath(receiptPath)),
                "resolved-inventory.json"), artifact.InventoryHash);
            var target = Object(inventory["target"]);
            var files = inventory["files"] as object[];
            if (Number(inventory, "schema_version") != 1 || Field(target, "os") != "Windows" ||
                Field(target, "architecture") != "x86_64" ||
                Field(Object(inventory["candidate"]), "id") != artifact.Candidate ||
                files == null || files.Length > 10000 || files.Length != Number(receipt, "payload_file_count"))
                throw new FormatException();

            long expanded = 0;
            foreach (object entry in files)
            {
                var record = Object(entry);
                string member = Field(record, "path");
                long size = Number(record, "size_bytes");
                if (!SafeMember(member) || member.Length > 500 || size < 0 || size > 2L * 1024 * 1024 * 1024 ||
                    !DigestPattern.IsMatch(Field(record, "sha256")) || artifact.Files.ContainsKey(member))
                    throw new FormatException();
                artifact.Files.Add(member, record);
                expanded += size;
            }
            if (expanded != Number(receipt, "payload_expanded_bytes") ||
                !Same(Field(artifact.Files[Terms], "sha256"), artifact.TermsHash))
                throw new FormatException();
            foreach (string required in new[] { Marker, Launcher, Desktop, "runtime/python.exe",
                "runtime/engines/qpdf/bin/qpdf.exe", "runtime/engines/tesseract/bin/tesseract.exe",
                "runtime/engines/tesseract/share/tessdata/eng.traineddata", "runtime/vcruntime140.dll",
                "runtime/vcruntime140_1.dll", "runtime/msvcp140.dll", "runtime/msvcp140_1.dll", "runtime/msvcp140_2.dll",
                "runtime/engines/qpdf/bin/msvcp140.dll", "runtime/engines/qpdf/bin/vcruntime140.dll",
                "runtime/engines/qpdf/bin/vcruntime140_1.dll" })
                if (!artifact.Files.ContainsKey(required)) throw new FormatException();

            // Keep a read-sharing handle open across native execution to prevent modification or replacement.
            artifact.Installer = new FileStream(artifact.InstallerPath, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (artifact.Installer.Length <= 0 || artifact.Installer.Length > 2L * 1024 * 1024 * 1024 ||
                artifact.Installer.Length != Number(receipt, "installer_size_bytes") ||
                !Same(Hash(artifact.Installer), artifact.InstallerHash))
                throw new FormatException();
        }
        catch (Exception error)
        {
            if (!(error is IOException || error is UnauthorizedAccessException || error is ArgumentException ||
                error is FormatException || error is KeyNotFoundException || error is InvalidOperationException)) throw;
            throw new SetupError("PWS_SETUP_ARTIFACT", "Copy the exact installer, build receipt and resolved inventory from the authorized package to a local drive. Use the published receipt SHA-256 and rerun setup.");
        }
    }

    private static int CheckPlatform()
    {
        const string versionKey = @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion";
        const string next = "Run setup on native x64 Windows 11.";
        int build = 0;
        Require(10.Equals(Registry.GetValue(versionKey, "CurrentMajorVersionNumber", 0)) &&
            Int32.TryParse(Registry.GetValue(versionKey, "CurrentBuildNumber", "") as string, out build) &&
            build >= 22000 && Same(Registry.GetValue(@"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\ProductOptions",
                "ProductType", "") as string, "WinNT"), "PWS_SETUP_PLATFORM", next);
        using (var current = Process.GetCurrentProcess())
        {
            ushort processMachine, nativeMachine;
            Require(IsWow64Process2(current.Handle, out processMachine, out nativeMachine) &&
                processMachine == 0 && nativeMachine == 0x8664, "PWS_SETUP_PLATFORM", next);
        }
        return build;
    }

    private static void CheckUserSession()
    {
        using (var identity = WindowsIdentity.GetCurrent())
            Require(!new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator),
                "PWS_SETUP_ELEVATED", "Run setup without administrator elevation in the intended user's account.");
        using (var current = Process.GetCurrentProcess())
            Require(Environment.UserInteractive && current.SessionId != 0,
                "PWS_SETUP_SESSION", "Run setup in the intended user's signed-in Windows desktop session.");
    }

    private static string MemberPath(string root, string member)
    {
        return Path.Combine(root, member.Replace('/', '\\'));
    }

    private static bool MemberMatches(string root, string member, Dictionary<string, object> record)
    {
        try
        {
            using (var file = new FileStream(LocalPath(MemberPath(root, member)), FileMode.Open, FileAccess.Read, FileShare.Read))
                return file.Length == Number(record, "size_bytes") && Same(Hash(file), Field(record, "sha256"));
        }
        catch (FileNotFoundException) { return false; }
        catch (DirectoryNotFoundException) { return false; }
    }

    private static bool HasRegistration(string root)
    {
        using (var key = Registry.CurrentUser.OpenSubKey(Registration))
        {
            if (key == null) return false;
            string location = key.GetValue("InstallLocation", "") as string ?? "";
            Require(location.Length == 0 || Same(location.TrimEnd('\\'), root), "PWS_SETUP_REGISTRATION_CONFLICT",
                "The current user's PaperWorkShell registration points to a different folder. Use the documented update or removal path for that installation before rerunning setup.");
            string uninstaller = key.GetValue("UninstallString", "") as string ?? "";
            if (uninstaller.StartsWith("\"", StringComparison.Ordinal) && uninstaller.EndsWith("\"", StringComparison.Ordinal) && uninstaller.Length > 2)
                uninstaller = uninstaller.Substring(1, uninstaller.Length - 2);
            return location.Length != 0 && uninstaller.IndexOf('"') < 0 && Same(Path.GetDirectoryName(uninstaller), root) &&
                Regex.IsMatch(Path.GetFileName(uninstaller), @"\Aunins[0-9]+\.exe\z", RegexOptions.IgnoreCase) &&
                File.Exists(LocalPath(uninstaller));
        }
    }

    private static bool HasShortcuts(string root)
    {
        string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
        Require(programs.Length != 0, "PWS_SETUP_PROFILE", "Sign in with a Windows user profile with a local Start menu, then rerun setup.");
        // The Shell COM object reads each existing shortcut. It never saves or launches it here.
        object shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell", true));
        try
        {
            foreach (string name in new[] { "PaperWorkShell", "PaperWorkShell notices" })
            {
                string path = LocalPath(Path.Combine(programs, name + ".lnk"));
                if (!File.Exists(path)) return false;
                dynamic link = ((dynamic)shell).CreateShortcut(path);
                try
                {
                    if (!Same((string)link.TargetPath, MemberPath(root, Launcher)) ||
                        !Same((string)link.WorkingDirectory, Path.Combine(root, "runtime")) ||
                        (string)link.Arguments != (name == "PaperWorkShell" ? "" : "--notices")) return false;
                }
                finally { Marshal.FinalReleaseComObject(link); }
            }
            return true;
        }
        finally { Marshal.FinalReleaseComObject(shell); }
    }

    private static string FindDifference(Artifact artifact, string root)
    {
        foreach (var entry in artifact.Files)
            if (!MemberMatches(root, entry.Key, entry.Value)) return entry.Key;
        if (!HasRegistration(root)) return "application registration";
        if (!HasShortcuts(root)) return "application shortcuts";
        return null;
    }

    private static List<Dictionary<string, object>> Running(string root)
    {
        var result = new List<Dictionary<string, object>>();
        using (var query = new ManagementObjectSearcher("SELECT ProcessId, ParentProcessId, SessionId, ExecutablePath, CommandLine FROM Win32_Process WHERE Name='PaperWorkShell.exe' OR Name='PaperWorkShellDesktop.exe'"))
        using (var matches = query.Get())
            foreach (ManagementObject item in matches)
                using (item)
                {
                    string path = item["ExecutablePath"] as string;
                    if (Same(path, MemberPath(root, Launcher)) || Same(path, MemberPath(root, Desktop)))
                        result.Add(new Dictionary<string, object> { { "pid", Convert.ToInt32(item["ProcessId"]) },
                            { "parent", Convert.ToInt32(item["ParentProcessId"]) }, { "session", Convert.ToInt32(item["SessionId"]) },
                            { "desktop", Same(path, MemberPath(root, Desktop)) }, { "command", item["CommandLine"] as string } });
                }
        return result;
    }

    private static void Install(Artifact artifact, string accepted, Dictionary<string, object> result)
    {
        bool quiet = accepted != null;
        string arguments = "/NORESTART /RESTARTEXITCODE=3010";
        if (quiet) arguments += " /VERYSILENT /SUPPRESSMSGBOXES /ACCEPTMICROSOFTRUNTIME=" + artifact.TermsHash;
        Console.Error.WriteLine("PaperWorkShell setup will install or repair {0} for this Windows user. Cases and exports remain separate.", artifact.Candidate);
        using (var setup = Process.Start(new ProcessStartInfo(artifact.InstallerPath, arguments)
            { UseShellExecute = true, WorkingDirectory = Path.GetDirectoryName(artifact.InstallerPath) }))
        {
            Require(setup != null, "PWS_SETUP_INSTALLER_START", "Open setup on an interactive desktop where Windows permits this exact package.");
            setup.WaitForExit();
            result["installerExitCode"] = setup.ExitCode;
            Require(setup.ExitCode != 3010 && setup.ExitCode != 8, "PWS_SETUP_RESTART",
                "The installer reported a restart requirement. Save your work, restart Windows when ready and rerun setup to finish verification.");
            Require(setup.ExitCode == 0, "PWS_SETUP_INSTALLER_EXIT",
                "Rerun without --accept-microsoft-terms to see the native installer message. Close PaperWorkShell before attempting a repair and keep the existing application files and case data.");
        }
    }

    private static void Launch(string root, Dictionary<string, object> result)
    {
        int session;
        using (var current = Process.GetCurrentProcess()) session = current.SessionId;
        var running = Running(root);
        int launcherId = 0;
        string entry = MemberPath(root, Launcher);
        foreach (var item in running)
            if (!(bool)item["desktop"] && (int)item["session"] == session)
            {
                string command = ((string)item["command"] ?? "").Trim();
                Require(launcherId == 0 && (Same(command, entry) || Same(command, "\"" + entry + "\"")),
                    "PWS_SETUP_APP_BUSY", "Close the PaperWorkShell windows normally and rerun setup.");
                launcherId = (int)item["pid"];
            }
        if (launcherId == 0)
        {
            using (var launcher = Process.Start(new ProcessStartInfo(entry)
                { UseShellExecute = true, WorkingDirectory = Path.GetDirectoryName(entry) }))
            {
                Require(launcher != null, "PWS_SETUP_LAUNCH_START", "Open PaperWorkShell from its Start menu shortcut to inspect the startup message, then rerun setup.");
                launcherId = launcher.Id;
            }
            result["launch"] = "started-installed-launcher";
        }
        else result["launch"] = "reused-installed-launcher";
        result["launcherPid"] = launcherId;

        var time = Stopwatch.StartNew();
        while (time.Elapsed < TimeSpan.FromSeconds(60))
        {
            foreach (var item in Running(root))
            {
                if (!(bool)item["desktop"] || (int)item["parent"] != launcherId || (int)item["session"] != session ||
                    !((string)item["command"] ?? "").EndsWith(" -I -B -m paperworkshell", StringComparison.Ordinal)) continue;
                try
                {
                    using (var desktop = Process.GetProcessById((int)item["pid"]))
                        if (desktop.MainWindowHandle != IntPtr.Zero && desktop.Responding)
                        {
                            var window = AutomationElement.FromHandle(desktop.MainWindowHandle).Current;
                            if (window.ProcessId != desktop.Id || window.IsOffscreen ||
                                window.AutomationId != "QApplication.paperWorkShellWindow") continue;
                            result["desktopPid"] = desktop.Id;
                            result["sessionId"] = session;
                            return;
                        }
                }
                catch (ArgumentException) { }
                catch (InvalidOperationException) { }
            }
            Thread.Sleep(500);
        }
        throw new SetupError("PWS_SETUP_WINDOW", "The verified launcher did not show a responsive PaperWorkShell desktop within 60 seconds. Inspect the visible startup message, close any PaperWorkShell dialogs normally and rerun setup.");
    }

    private static Dictionary<string, string> Parse(string[] args)
    {
        var options = new Dictionary<string, string>(StringComparer.Ordinal);
        string[] values = { "--installer", "--receipt", "--receipt-sha256", "--accept-microsoft-terms", "--result" };
        for (int i = 0; i < args.Length; ++i)
        {
            string name = args[i];
            if (name == "--repair" && !options.ContainsKey(name)) options.Add(name, null);
            else if (Array.IndexOf(values, name) >= 0 && !options.ContainsKey(name) && i + 1 < args.Length && args[i + 1].Length != 0)
                options.Add(name, args[++i]);
            else throw new SetupError("PWS_SETUP_ARGUMENTS", Usage);
        }
        Require(options.ContainsKey("--installer") && options.ContainsKey("--receipt") &&
            options.ContainsKey("--receipt-sha256") && DigestPattern.IsMatch(options["--receipt-sha256"]),
            "PWS_SETUP_ARGUMENTS", Usage);
        return options;
    }

    private const string Usage = "Setup-PaperWorkShell.exe --installer <Setup.exe> --receipt <installer-receipt.json> --receipt-sha256 <published SHA-256> [--accept-microsoft-terms <reviewed terms SHA-256>] [--repair] [--result <new JSON file>]";

    [STAThread]
    public static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--help") { Console.WriteLine(Usage); return 0; }
        var result = new Dictionary<string, object> { { "schemaVersion", 1 }, { "status", "incomplete" } };
        StreamWriter output = null;
        try
        {
            var options = Parse(args);
            result["windowsBuild"] = CheckPlatform();
            string value;
            if (options.TryGetValue("--result", out value))
            {
                try { output = new StreamWriter(new FileStream(LocalPath(value), FileMode.CreateNew, FileAccess.Write, FileShare.Read), new UTF8Encoding(false)); }
                catch (Exception error)
                {
                    if (!(error is IOException || error is UnauthorizedAccessException || error is ArgumentException)) throw;
                    throw new SetupError("PWS_SETUP_RESULT", "Choose a new result file in an existing local directory and rerun setup.");
                }
            }
            using (var artifact = new Artifact())
            {
                OpenArtifact(artifact, options["--installer"], options["--receipt"], options["--receipt-sha256"]);
                result["candidate"] = artifact.Candidate;
                result["installerSha256"] = artifact.InstallerHash;
                result["receiptSha256"] = options["--receipt-sha256"].ToLowerInvariant();
                result["inventorySha256"] = artifact.InventoryHash;
                string accepted;
                options.TryGetValue("--accept-microsoft-terms", out accepted);
                Require(accepted == null || Same(accepted, artifact.TermsHash), "PWS_SETUP_TERMS",
                    "Review the package's Microsoft runtime recipient terms. Run setup without --accept-microsoft-terms for interactive acceptance, or explicitly provide the exact accepted terms SHA-256.");
                CheckUserSession();
                string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                Require(local.Length != 0, "PWS_SETUP_PROFILE", "Sign in with a Windows user profile with local application storage, then rerun setup.");
                string root = LocalPath(Path.Combine(local, @"Programs\PaperWorkShell"));
                bool installed = Directory.Exists(root);
                Require(!File.Exists(root), "PWS_SETUP_DESTINATION", "The fixed per-user installation path is occupied by a file. Preserve it and resolve that path conflict before rerunning setup.");
                if (installed)
                {
                    Require(File.Exists(LocalPath(MemberPath(root, Marker))), "PWS_SETUP_UNIDENTIFIED_INSTALLATION",
                        "The existing PaperWorkShell folder has no installation marker. Preserve it and recover the interrupted installation from its known-good state before rerunning setup.");
                    Require(MemberMatches(root, Marker, artifact.Files[Marker]), "PWS_SETUP_EXISTING_INSTALLATION",
                        "The existing PaperWorkShell folder has a different installation marker. Keep it intact and use its supported update or removal path before rerunning setup.");
                }
                HasRegistration(root); // Check for a conflicting registered location even when the fixed folder is absent.
                string difference = installed ? FindDifference(artifact, root) : null;
                if (difference != null) result["firstDifference"] = difference;
                if (!installed || difference != null || options.ContainsKey("--repair"))
                {
                    Require(Running(root).Count == 0, "PWS_SETUP_APP_BUSY", "Close PaperWorkShell and its notices or removal review normally, then rerun setup.");
                    result["operation"] = installed ? "repair" : "install";
                    Install(artifact, accepted, result);
                    difference = FindDifference(artifact, root);
                    if (difference != null) result["verificationDifference"] = difference;
                    Require(difference == null, "PWS_SETUP_VERIFY", "The installer exited but a payload file, registration or shortcut still differs. Close PaperWorkShell and rerun with --repair. Keep existing case data and check the reported difference if repair fails again.");
                }
                else result["operation"] = "reuse";
                result["verifiedFiles"] = artifact.Files.Count;
                result["markerSha256"] = Field(artifact.Files[Marker], "sha256");
                Launch(root, result);
                result["status"] = "verified";
            }
        }
        catch (SetupError error)
        {
            result["code"] = error.Code;
            result["nextAction"] = error.Message;
            Console.Error.WriteLine("{0} {1}", error.Code, error.Message);
        }
        catch (System.ComponentModel.Win32Exception error)
        {
            result["code"] = "PWS_SETUP_WINDOWS_EXECUTION";
            result["nativeError"] = error.NativeErrorCode;
            result["nextAction"] = "Check the Windows execution message. Rerun in the intended Windows desktop only when its policy permits this exact package.";
        }
        catch (Exception error)
        {
            result["code"] = "PWS_SETUP_ENVIRONMENT";
            result["errorType"] = error.GetType().Name;
            result["nextAction"] = "Check access to the fixed local installation folder and the signed-in Windows desktop. Rerun setup after resolving the access error.";
        }
        string json = Json.Serialize(result);
        Console.WriteLine(json);
        if (output != null) using (output) output.WriteLine(json);
        return (string)result["status"] == "verified" ? 0 : 1;
    }
}
