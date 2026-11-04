using Watchman.Core;

namespace Watchman.Service;

/// <summary>Pairing and status from an elevated command prompt; the service picks the files up.</summary>
public static class Cli
{
    public static async Task<int> RunAsync(string[] args)
    {
        var store = new ProtectedFileStore(Paths.DataDir);
        var state = new ServiceState(store, new SystemClock());
        switch (args[0])
        {
            case "pair":
            {
                var server = Arg(args, "--server");
                var code = Arg(args, "--code");
                if (server is null || code is null) { Console.Error.WriteLine("usage: pair --server URL --code CODE [--name NAME]"); return 2; }
                var name = Arg(args, "--name") ?? Environment.MachineName;
                try
                {
                    var pairing = await Api.PairAsync(server, code, name);
                    state.Pairing = pairing;
                    using var api = new Api(pairing.Server, pairing.Token);
                    state.Policy = await api.PolicyAsync();
                    await state.EnsureDetectorAsync(api, Console.WriteLine);
                    Console.WriteLine($"paired with {pairing.ServerName} as {name}; the service will start reporting.");
                    return 0;
                }
                catch (ApiException ex) { Console.Error.WriteLine($"{ex.Code}: {ex.Message}"); return 1; }
            }
            case "unpair":
                state.Pairing = null;
                state.Policy = null;
                Console.WriteLine("unpaired; the server will report this device silent within half an hour.");
                return 0;
            default:
            {
                var p = state.Pairing;
                Console.WriteLine(p is null ? "not paired" : $"paired with {p.ServerName} ({p.Server})");
                if (p is not null && state.Rejected) Console.WriteLine($"rejected: {ServiceState.RejectedMessage}");
                Console.WriteLine($"policy version: {state.Policy?.Version.ToString() ?? "none"}; paused until: {state.Policy?.PausedUntil ?? "no"}");
                Console.WriteLine($"detector: {state.DetectorPath ?? "not downloaded"}");
                Console.WriteLine($"queued events: {state.Queue.Count}");
                return 0;
            }
        }
    }

    private static string? Arg(string[] args, string name)
    {
        var i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
    }
}
