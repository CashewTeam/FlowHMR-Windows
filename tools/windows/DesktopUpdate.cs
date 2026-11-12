using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text.RegularExpressions;
using System.Threading;

// The launcher activates a completed update while the Python runtime is closed.
internal sealed class DesktopUpdate
{
    private const string VersionFile = "FlowHMR-Version.txt";
    private static readonly Regex VersionPattern = new Regex(@"\A(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}\z");
    private static readonly Regex ReservedName = new Regex(@"\A(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|\z)", RegexOptions.IgnoreCase);
    private static readonly HashSet<string> CodeDirectories = new HashSet<string> { "flowhmr", "tools", "third_party" };
    private static readonly HashSet<string> SharedDirectories = new HashSet<string> { "assets", "configs", "data", "docs" };
    private static readonly HashSet<string> RootFiles = new HashSet<string>
    {
        VersionFile, ".gitignore", ".gitmodules", "app.py", "check-install.cmd", "download-models.cmd",
        "LICENSE", "README.md", "requirements-phc.txt", "requirements-windows.lock.txt", "requirements-windows.txt",
        "requirements.txt", "run_v2m_demo_generation.py", "start-web.cmd", "stop-web.cmd", "THIRD_PARTY_NOTICES.md", "train_v2m.py",
    };

    private readonly string root;
    private readonly string work;
    private readonly string ready;
    private readonly string applying;
    private readonly string previous;
    private readonly string undoing;
    private readonly string forward;

    internal DesktopUpdate(string root)
    {
        this.root = Path.GetFullPath(root);
        work = Path.Combine(this.root, ".cache", "program-update");
        ready = Path.Combine(work, "ready");
        applying = Path.Combine(work, "applying");
        previous = Path.Combine(work, "previous");
        undoing = Path.Combine(work, "undoing");
        forward = Path.Combine(work, "forward");
    }

    internal bool Enabled
    {
        get { return File.Exists(Path.Combine(root, VersionFile)) || Directory.Exists(applying) || Directory.Exists(ready) || Directory.Exists(undoing); }
    }

    private static FileAttributes? Attributes(string path)
    {
        try { return File.GetAttributes(path); }
        catch (FileNotFoundException) { return null; }
        catch (DirectoryNotFoundException) { return null; }
    }

    private static string SafePath(string parent, string relative)
    {
        string prefix = Path.GetFullPath(parent).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        string result = Path.GetFullPath(Path.Combine(prefix, relative));
        if (!result.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("An update path is outside its application directory.");
        string next = prefix.TrimEnd(Path.DirectorySeparatorChar);
        foreach (string part in relative.Split('/'))
        {
            next = Path.Combine(next, part);
            if ((Attributes(next).GetValueOrDefault() & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("An update path points to a junction or symbolic link: " + next);
        }
        return result;
    }

    private static bool IsTarget(string relative)
    {
        string[] parts = relative.Split('/');
        foreach (string part in parts)
        {
            if (part.Length == 0 || part.EndsWith(".", StringComparison.Ordinal) || part.EndsWith(" ", StringComparison.Ordinal)
                || part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || ReservedName.IsMatch(part)) return false;
        }
        string top = parts[0];
        if (parts.Length == 1) return RootFiles.Contains(top) || CodeDirectories.Contains(top);
        return SharedDirectories.Contains(top) || (parts.Length == 2 && top == "runtime"
            && !parts[1].Equals("environments", StringComparison.OrdinalIgnoreCase)
            && !parts[1].Equals("webview2", StringComparison.OrdinalIgnoreCase));
    }

    private static Version ReadVersion(string directory, string name = VersionFile)
    {
        string path = SafePath(directory, name);
        if (!File.Exists(path) || new FileInfo(path).Length > 40) return null;
        string value = File.ReadAllText(path).Trim();
        return VersionPattern.IsMatch(value) ? new Version(value) : null;
    }

    private static string[] ReadPlan(string directory)
    {
        string path = SafePath(directory, "targets.txt");
        if (new FileInfo(path).Length > (1 << 20)) throw new InvalidDataException("Update file list is too large.");
        string[] targets = File.ReadAllLines(path);
        var unique = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (string target in targets)
            if (!IsTarget(target) || !unique.Add(target)) throw new InvalidDataException("Invalid update target: " + target);
        foreach (string target in targets)
            for (int slash = target.IndexOf('/'); slash >= 0; slash = target.IndexOf('/', slash + 1))
                if (unique.Contains(target.Substring(0, slash))) throw new InvalidDataException("Overlapping update targets.");
        if (targets.Length == 0 || targets[targets.Length - 1] != VersionFile)
            throw new InvalidDataException("The update file list has no version marker.");
        return targets;
    }

    private static void Move(string source, string destination)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(destination));
        if (Directory.Exists(source)) Directory.Move(source, destination);
        else File.Move(source, destination);
    }

    private static string Staged(string transaction, string target) { return SafePath(transaction, "files/" + target); }
    private static string Backup(string transaction, string target) { return SafePath(transaction, "backup/" + target); }

    private void Retire(string transaction)
    {
        try { Directory.Move(transaction, Path.Combine(work, "retired-" + Guid.NewGuid().ToString("N"))); }
        catch (IOException error) { Record(error); }
        catch (UnauthorizedAccessException error) { Record(error); }
    }

    internal void Record(Exception error)
    {
        try { File.AppendAllText(Path.Combine(work, "update.log"), DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) + " " + error + Environment.NewLine); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private void RollBack(string transaction)
    {
        string[] targets = ReadPlan(transaction);
        Array.Reverse(targets);
        foreach (string target in targets)
        {
            string live = SafePath(root, target);
            string stage = Staged(transaction, target);
            string original = Backup(transaction, target);
            if (!Attributes(stage).HasValue && Attributes(live).HasValue) Move(live, stage);
            if (Attributes(original).HasValue) Move(original, live);
        }
    }

    private void RestoreFiles(string transaction)
    {
        // The desktop owns a Windows job. Give Windows a moment to release its handles.
        for (int attempt = 0; ; attempt++)
        {
            try { RollBack(transaction); break; }
            catch (IOException) { if (attempt >= 5) throw; Thread.Sleep(150); }
            catch (UnauthorizedAccessException) { if (attempt >= 5) throw; Thread.Sleep(150); }
        }
    }

    internal bool Finish()
    {
        bool healthy = File.Exists(SafePath(applying, "healthy"));
        if (!healthy) RestoreFiles(applying);
        else
        {
            if (Directory.Exists(forward)) Retire(forward);
            if (Directory.Exists(previous)) Retire(previous);
            if (!Directory.Exists(previous))
            {
                try { Directory.Move(applying, previous); return true; }
                catch (IOException error) { Record(error); }
                catch (UnauthorizedAccessException error) { Record(error); }
            }
        }
        Retire(applying);
        return healthy;
    }

    private void ApplySavedVersion()
    {
        string request = SafePath(work, "rollback-request.txt");
        if (!Directory.Exists(undoing) && File.Exists(request))
        {
            Version expected = ReadVersion(work, "rollback-request.txt");
            Version saved = ReadVersion(SafePath(previous, "backup"));
            Version installed = ReadVersion(root);
            if (expected != null && expected.Equals(saved) && installed != null
                && installed.Equals(ReadVersion(previous, "installed-version.txt")) && !Directory.Exists(ready))
                Directory.Move(previous, undoing);
            File.Delete(request);
        }
        if (!Directory.Exists(undoing)) return;
        RestoreFiles(undoing);
        File.Delete(SafePath(undoing, "healthy"));
        if (Directory.Exists(forward)) Retire(forward);
        try { Directory.Move(undoing, forward); }
        catch (IOException error) { Record(error); Retire(undoing); }
        catch (UnauthorizedAccessException error) { Record(error); Retire(undoing); }
        if (File.Exists(request)) File.Delete(request);
    }

    internal bool Prepare()
    {
        // A cached install is optional. An unavailable cache never prevents ordinary startup.
        try
        {
            SafePath(root, ".cache/program-update");
            SafePath(work, "applying");
            SafePath(work, "ready");
            SafePath(work, "previous");
            SafePath(work, "undoing");
            SafePath(work, "forward");
        }
        catch (InvalidDataException) { return false; }
        catch (IOException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
        if (Directory.Exists(applying))
        {
            Finish();
            if (Directory.Exists(applying)) return false;
        }
        ApplySavedVersion();
        if (!Directory.Exists(ready)) return false;
        string[] targets;
        try
        {
            Version installed = ReadVersion(root), candidate = ReadVersion(SafePath(ready, "files"));
            if (installed == null || candidate == null || candidate.Equals(installed)
                || (candidate.CompareTo(installed) < 0 && !candidate.Equals(ReadVersion(ready, "allow-downgrade"))))
                throw new InvalidDataException("The staged version cannot be installed over this version.");
            targets = ReadPlan(ready);
            foreach (string target in targets)
            {
                SafePath(root, target);
                if (!Attributes(Staged(ready, target)).HasValue)
                    throw new InvalidDataException("The staged update is incomplete: " + target);
            }
            File.WriteAllText(SafePath(ready, "installed-version.txt"), candidate.ToString());
            Directory.Move(ready, applying);
        }
        catch (Exception error)
        {
            if (!(error is IOException || error is InvalidDataException || error is UnauthorizedAccessException)) throw;
            Record(error);
            Retire(ready);
            return false;
        }
        try
        {
            foreach (string target in targets)
            {
                string live = SafePath(root, target);
                if (Attributes(live).HasValue) Move(live, Backup(applying, target));
                Move(Staged(applying, target), live);
            }
            return true;
        }
        catch (Exception error)
        {
            if (!(error is IOException || error is InvalidDataException || error is UnauthorizedAccessException)) throw;
            Record(error);
            Finish();
            return false;
        }
    }
}
