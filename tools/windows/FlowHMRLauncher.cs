using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("FlowHMR")]
[assembly: AssemblyProduct("FlowHMR")]

internal static class FlowHMRLauncher
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr FindWindow(string className, string title);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr window, int command);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr window);

    private static bool HasWebView2()
    {
        const string client = @"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";
        foreach (RegistryHive hive in new[] { RegistryHive.LocalMachine, RegistryHive.CurrentUser })
        foreach (RegistryView view in new[] { RegistryView.Registry32, RegistryView.Registry64 })
        using (var root = RegistryKey.OpenBaseKey(hive, view))
        using (var key = root.OpenSubKey(client))
        {
            var version = key == null ? null : key.GetValue("pv") as string;
            if (!String.IsNullOrEmpty(version) && version != "0.0.0.0") return true;
        }
        return false;
    }

    private static void InstallPrerequisite(string path, string arguments, string name)
    {
        if (!File.Exists(path)) throw new FileNotFoundException("发布包缺少 " + name + " 安装组件。");
        using (var process = Process.Start(new ProcessStartInfo(path, arguments)
        {
            UseShellExecute = true,
            WindowStyle = ProcessWindowStyle.Hidden,
        }))
        {
            process.WaitForExit();
            if (process.ExitCode != 0 && process.ExitCode != 3010 && process.ExitCode != 1638)
                throw new InvalidOperationException(name + " 安装失败，退出码 " + process.ExitCode + "。");
        }
    }

    private static void PreparePortableRuntime(string root)
    {
        string runtime = Path.Combine(root, "runtime");
        string python = Path.Combine(runtime, "python.exe");
        if (!File.Exists(python)) return;
        string prerequisites = Path.Combine(runtime, "prerequisites");
        if (!HasWebView2() && !File.Exists(Path.Combine(runtime, "webview2", "msedgewebview2.exe")))
            InstallPrerequisite(Path.Combine(prerequisites, "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"),
                "/silent /install", "Microsoft WebView2");
        if (!File.Exists(Path.Combine(Environment.SystemDirectory, "msvcp140.dll")))
            InstallPrerequisite(Path.Combine(prerequisites, "vc_redist.x64.exe"),
                "/install /quiet /norestart", "Microsoft Visual C++ Runtime");
    }

    private static string PythonGui(string root)
    {
        var portable = Path.Combine(root, "runtime", "pythonw.exe");
        if (File.Exists(portable)) return portable;

        var config = Path.Combine(root, ".venv", "pyvenv.cfg");
        if (File.Exists(config))
        {
            foreach (var line in File.ReadLines(config))
            {
                var pair = line.Split(new[] { '=' }, 2);
                if (pair.Length != 2 || pair[0].Trim() != "home") continue;
                var interpreter = Path.Combine(pair[1].Trim(), "pythonw.exe");
                if (File.Exists(interpreter)) return interpreter;
            }
        }
        throw new FileNotFoundException("未找到 FlowHMR 运行环境。请完整解压 Windows 发布包，或按源码安装说明准备 .venv。");
    }

    private static int RunDesktop(string root, int port, bool trial)
    {
        var script = Path.Combine(root, "tools", "windows", "desktop_launcher.pyw");
        if (!File.Exists(script)) throw new FileNotFoundException("发布包不完整，请将所有文件解压到同一个目录。");
        PreparePortableRuntime(root);
        var start = new ProcessStartInfo(PythonGui(root), "-I -B \"" + script + "\" --port " + port)
        {
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        string fixedWebview = Path.Combine(root, "runtime", "webview2");
        if (File.Exists(Path.Combine(fixedWebview, "msedgewebview2.exe")))
            start.EnvironmentVariables["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] = fixedWebview;
        start.EnvironmentVariables.Remove("FLOWHMR_UPDATE_TRIAL");
        if (trial) start.EnvironmentVariables["FLOWHMR_UPDATE_TRIAL"] = "1";
        using (var app = Process.Start(start))
        {
            app.WaitForExit();
            return app.ExitCode;
        }
    }

    private static int RunPortable(string root, int port, DesktopUpdate update)
    {
        string key;
        using (var sha = SHA256.Create())
            key = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant())));
        using (var instance = new Mutex(false, @"Local\FlowHMR-portable-" + key))
        {
            bool acquired;
            try { acquired = instance.WaitOne(0); }
            catch (AbandonedMutexException) { acquired = true; }
            if (!acquired)
            {
                IntPtr window = FindWindow(null, "FlowHMR");
                if (window != IntPtr.Zero) { ShowWindow(window, 9); SetForegroundWindow(window); }
                return 0;
            }
            try
            {
                if (!update.Prepare()) return RunDesktop(root, port, false);
                int code;
                try { code = RunDesktop(root, port, true); }
                catch (Exception error) { update.Record(error); code = 1; }
                if (update.Finish() || code == 0) return code;
                return RunDesktop(root, port, false);
            }
            finally { instance.ReleaseMutex(); }
        }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            int port = 60550;
            if (args.Length != 0 && (args.Length != 2 || args[0] != "--port" ||
                !int.TryParse(args[1], out port) || port < 1 || port > 65535))
                throw new ArgumentException("用法：FlowHMR.exe [--port 端口]");

            string root = AppDomain.CurrentDomain.BaseDirectory;
            var update = new DesktopUpdate(root);
            return update.Enabled ? RunPortable(root, port, update) : RunDesktop(root, port, false);
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "FlowHMR", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
