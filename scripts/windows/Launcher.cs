// A Windows console entry point: stdio bytes are inherited, never decoded.
// .NET Framework 4.x is provided by supported Windows installations.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

class Launcher {
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static readonly string Base = AppDomain.CurrentDomain.BaseDirectory;
    static readonly string Settings = Path.Combine(Base, "openmc-forge.local.json");
    static readonly string Wsl = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "wsl.exe");

    // CommandLineToArgvW-compatible quoting; no cmd.exe or interpolated shell.
    static string Quote(string value) {
        if (value.Length > 0 && value.IndexOfAny(new char[] { ' ', '\t', '\n', '\r', '"' }) < 0) return value;
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(c); slashes = 0;
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }
    static string Arguments(List<string> args) {
        return String.Join(" ", args.ConvertAll(Quote).ToArray());
    }
    static List<string> Command(string distribution, params string[] command) {
        var args = new List<string>();
        if (!String.IsNullOrEmpty(distribution)) { args.Add("--distribution"); args.Add(distribution); }
        args.Add("--exec"); args.AddRange(command); return args;
    }
    static void CopyFlushed(Stream input, Stream output) {
        byte[] buffer = new byte[8192]; int count;
        while ((count = input.Read(buffer,0,buffer.Length)) > 0) {
            output.Write(buffer,0,count); output.Flush();
        }
    }
    static int Run(List<string> args, bool capture, out string output, bool protocol = false) {
        var info = new ProcessStartInfo(Wsl, Arguments(args));
        info.UseShellExecute = false;
        info.RedirectStandardOutput = capture || protocol;
        if (info.RedirectStandardOutput) info.StandardOutputEncoding = Encoding.UTF8;
        info.RedirectStandardInput = protocol;
        using (var job = new ChildJob())
        using (var process = Process.Start(info)) {
            try { job.Attach(process); }
            catch { process.Kill(); process.WaitForExit(); throw; }
            Task<bool> stdout = null;
            if (protocol) {
                Task.Run(() => {
                    try { CopyFlushed(Console.OpenStandardInput(),process.StandardInput.BaseStream); }
                    catch (IOException) { }
                    catch (ObjectDisposedException) { }
                    finally { try { process.StandardInput.Close(); } catch { } }
                });
                stdout = Task.Run(() => {
                    var input = process.StandardOutput.BaseStream;
                    int first = input.ReadByte();
                    if (first < 0) return true;
                    if (first != '{') {
                        // WSL itself writes UTF-16 startup errors to stdout.
                        // Never send those bytes into the MCP protocol channel.
                        Console.Error.WriteLine("WSL did not start MCP. Check wsl --list --verbose and rerun openmc-forge.exe setup with the correct --distribution/--root.");
                        input.CopyTo(Stream.Null); return false;
                    }
                    var destination = Console.OpenStandardOutput();
                    destination.WriteByte((byte)first); destination.Flush();
                    CopyFlushed(input,destination); return true;
                });
            }
            output = capture ? process.StandardOutput.ReadToEnd().Trim() : null;
            process.WaitForExit();
            return protocol && !stdout.Result ? 2 : process.ExitCode;
        }
    }
    static Dictionary<string, string> Load() {
        return File.Exists(Settings) ? Json.Deserialize<Dictionary<string,string>>(File.ReadAllText(Settings)) : new Dictionary<string,string>();
    }
    static void Save(Dictionary<string,string> config) {
        string pending = Settings + ".tmp";
        File.WriteAllText(pending, Json.Serialize(config), new UTF8Encoding(false));
        if (File.Exists(Settings)) File.Replace(pending, Settings, null);
        else File.Move(pending, Settings);
    }
    static string Get(Dictionary<string,string> config, string name) {
        string value; return config.TryGetValue(name, out value) ? value : "";
    }
    static void PrintConfig() {
        var entry = new Dictionary<string,object>();
        entry["command"] = Process.GetCurrentProcess().MainModule.FileName;
        entry["args"] = new string[0];
        Console.WriteLine(Json.Serialize(new { mcpServers = new Dictionary<string,object> { {"openmc-forge", entry} } }));
    }
    static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        try {
            string mode = args.Length == 0 ? "stdio" : args[0];
            if (mode == "--version") {
                Console.WriteLine(((AssemblyInformationalVersionAttribute)Attribute.GetCustomAttribute(Assembly.GetExecutingAssembly(),typeof(AssemblyInformationalVersionAttribute))).InformationalVersion); return 0;
            }
            if (mode == "--help" || mode == "help" || mode == "-h") {
                Console.WriteLine("OpenMC Forge: setup [--distribution NAME] [--root /linux/package] [--workspace /linux/workspace] [--openmc-prefix /linux/env] [--install-runtime] [--with-cad] [--with-office]");
                Console.WriteLine("Run setup once, then configure the MCP host with the JSON printed by config. No arguments starts stdio. Linux: bash scripts/local-setup.sh; bash mcp/stdio.sh. HTTP remains available.");
                return 0;
            }
            if (mode == "config") { if(args.Length != 1)throw new ArgumentException("config takes no options"); PrintConfig(); return 0; }
            if (mode != "setup" && mode != "stdio") throw new ArgumentException("Use setup, config, stdio or --help.");
            if (!File.Exists(Wsl)) throw new IOException("WSL is unavailable. Install WSL and a Linux distribution first (wsl --install), finish Linux account creation, then rerun setup.");
            var config = Load(); var setup = new List<string>();
            for (int i = 1; i < args.Length; i++) {
                string option = args[i];
                if (option == "--distribution" || option == "--root" || option == "--workspace" || option == "--openmc-prefix") {
                    if (++i >= args.Length || args[i].Length == 0)throw new ArgumentException("Missing value for " + option);
                    if (option == "--distribution" || option == "--root" || option == "--workspace") { config[option.Substring(2)] = args[i]; if(option=="--root")config["managed"]="false"; }
                    else { setup.Add(option); setup.Add(args[i]); }
                } else if (option == "--install-runtime" || option == "--with-cad" || option == "--with-office") setup.Add(option);
                else throw new ArgumentException("Unknown option: " + option);
            }
            if (mode == "stdio" && args.Length != 1 && args.Length != 0) throw new ArgumentException("Run setup to change configuration; stdio takes no options.");
            string distribution = Get(config,"distribution"), root = Get(config,"root"), output;
            string launcherVersion = ((AssemblyInformationalVersionAttribute)Attribute.GetCustomAttribute(Assembly.GetExecutingAssembly(),typeof(AssemblyInformationalVersionAttribute))).InformationalVersion;
            string previousRoot = root;
            if (mode=="stdio" && Get(config,"managed")=="true" && Get(config,"launcher_version")!=launcherVersion)
                throw new IOException("Launcher version changed. Run openmc-forge.exe setup before reconnecting.");
            if (mode=="setup" && Get(config,"managed")=="true") root="";
            if (String.IsNullOrEmpty(root)) {
                if (mode != "setup") throw new IOException("Not configured. Run openmc-forge.exe setup first.");
                // The distributed exe is next to quickstart.sh in the full bundle.
                if (!File.Exists(Path.Combine(Base,"quickstart.sh"))) throw new IOException("Full toolkit not found next to exe. Extract the Windows bundle, or use setup --root /linux/package.");
                int converted = Run(Command(distribution,"wslpath","-a",Base.TrimEnd('\\')),true,out output);
                if (converted != 0 || String.IsNullOrEmpty(output)) throw new IOException("Cannot resolve toolkit path in WSL. Check wsl --list --verbose and --distribution.");
                string source = output;
                int location = Run(Command(distribution,"/usr/bin/printenv","XDG_DATA_HOME"),true,out output);
                if (location != 0 || String.IsNullOrEmpty(output)) {
                    location = Run(Command(distribution,"/usr/bin/printenv","HOME"),true,out output);
                    if (location != 0 || !output.StartsWith("/")) throw new IOException("Cannot locate Linux user data directory.");
                    output += "/.local/share";
                }
                if (!output.StartsWith("/")) throw new IOException("XDG_DATA_HOME must be an absolute Linux path.");
                string dataRoot = output.TrimEnd('/') + "/openmc-forge";
                root = dataRoot + "/" + launcherVersion;
                if (String.IsNullOrEmpty(Get(config,"workspace"))) config["workspace"] = dataRoot + "/workspace";
                config["managed"] = "true";
                var install = Command(distribution,"python3",source.TrimEnd('/')+"/scripts/install_local.py","--source",source,"--target",root);
                if (!String.IsNullOrEmpty(previousRoot)) { install.Add("--reuse-from");install.Add(previousRoot); }
                int copied = Run(install,false,out output);
                if (copied != 0) return copied;
                config["root"] = root;
            }
            if (!root.StartsWith("/") || root.Contains("\n") || root.Contains("\r")) throw new ArgumentException("--root must be an absolute Linux path.");
            var command = Command(distribution,"/bin/bash",root.TrimEnd('/') + (mode == "setup" ? "/scripts/local-setup.sh" : "/mcp/stdio.sh"));
            if (mode == "setup") {
                command.Add("--no-connection-output"); command.AddRange(setup);
                if (!String.IsNullOrEmpty(Get(config,"workspace"))) { command.Add("--workspace"); command.Add(config["workspace"]); }
            }
            int result = Run(command,false,out output,mode == "stdio");
            if (result == 0 && mode == "setup") {
                // Pin the actual default distro: later changes to WSL's default
                // must not silently switch this connection to another workspace.
                if (String.IsNullOrEmpty(distribution)) {
                    result = Run(Command("","/usr/bin/printenv","WSL_DISTRO_NAME"),true,out output);
                    if (result != 0 || String.IsNullOrEmpty(output)) throw new IOException("Cannot identify WSL distribution; rerun setup --distribution NAME.");
                    config["distribution"] = output;
                }
                config["launcher_version"] = launcherVersion; Save(config);
                Console.WriteLine("Setup complete. MCP host configuration:"); PrintConfig();
            }
            return result;
        } catch (Exception error) {
            Console.Error.WriteLine("OpenMC Forge launcher: " + error.Message); return 2;
        }
    }

    // Close only this launcher's Windows subprocess when the host kills us.
    // Never terminate a WSL distribution or cancel detached Linux compute jobs.
    sealed class ChildJob : IDisposable {
        IntPtr handle;
        [StructLayout(LayoutKind.Sequential)] struct Basic {
            public long ProcessTime, JobTime; public uint Flags;
            public UIntPtr MinWorkingSet, MaxWorkingSet; public uint ActiveProcesses;
            public UIntPtr Affinity; public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] struct IO {
            public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
        }
        [StructLayout(LayoutKind.Sequential)] struct Extended {
            public Basic Basic; public IO Io; public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes,string name);
        [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int kind,ref Extended info,uint length);
        [DllImport("kernel32.dll",SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        public ChildJob() {
            handle=CreateJobObject(IntPtr.Zero,null); if(handle==IntPtr.Zero)throw new IOException("Cannot create launcher process job.");
            var info=new Extended();info.Basic.Flags=0x2000; // KILL_ON_JOB_CLOSE
            if(!SetInformationJobObject(handle,9,ref info,(uint)Marshal.SizeOf(typeof(Extended)))){Dispose();throw new IOException("Cannot set launcher process lifetime.");}
        }
        public void Attach(Process process) {
            if(!AssignProcessToJobObject(handle,process.Handle))throw new IOException("Cannot attach WSL child to launcher job.");
        }
        public void Dispose() { if(handle!=IntPtr.Zero){CloseHandle(handle);handle=IntPtr.Zero;} }
    }
}
