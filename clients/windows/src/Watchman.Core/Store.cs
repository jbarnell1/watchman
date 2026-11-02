using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Watchman.Core;

/// <summary>Where the service keeps its files. Everything sensitive goes through Protect.</summary>
public interface IStore
{
    string? ReadText(string name);
    void WriteText(string name, string text);
    void Delete(string name);
}

public sealed class MemoryStore : IStore
{
    private readonly Dictionary<string, string> _data = new();
    public string? ReadText(string name) => _data.GetValueOrDefault(name);
    public void WriteText(string name, string text) => _data[name] = text;
    public void Delete(string name) => _data.Remove(name);
}

/// <summary>Files under ProgramData\Watchman, sealed with DPAPI (machine scope) so only this machine reads them.</summary>
public sealed class ProtectedFileStore(string directory) : IStore
{
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("watchman-windows-v1");

    public string? ReadText(string name)
    {
        var path = Path.Combine(directory, name);
        if (!File.Exists(path)) return null;
        var sealedBytes = File.ReadAllBytes(path);
        var plain = ProtectedData.Unprotect(sealedBytes, Entropy, DataProtectionScope.LocalMachine);
        return Encoding.UTF8.GetString(plain);
    }

    public void WriteText(string name, string text)
    {
        Directory.CreateDirectory(directory);
        var sealedBytes = ProtectedData.Protect(Encoding.UTF8.GetBytes(text), Entropy, DataProtectionScope.LocalMachine);
        var tmp = Path.Combine(directory, name + ".tmp");
        File.WriteAllBytes(tmp, sealedBytes);
        File.Move(tmp, Path.Combine(directory, name), overwrite: true);
    }

    public void Delete(string name)
    {
        var path = Path.Combine(directory, name);
        if (File.Exists(path)) File.Delete(path);
    }
}

public static class StoreExtensions
{
    public static T? ReadJson<T>(this IStore store, string name) where T : class
    {
        var text = store.ReadText(name);
        return text is null ? null : JsonSerializer.Deserialize<T>(text, Json.Options);
    }

    public static void WriteJson<T>(this IStore store, string name, T value) =>
        store.WriteText(name, JsonSerializer.Serialize(value, Json.Options));
}

/// <summary>Events wait here until the server takes them. At most 200 per post, 5000 kept.</summary>
public sealed class EventQueue(IStore store)
{
    public const int MaxBatch = 200;
    public const int MaxQueue = 5000;
    private const string Name = "queue.json";
    private readonly object _lock = new();

    public List<WatchmanEvent> All()
    {
        lock (_lock) return store.ReadJson<List<WatchmanEvent>>(Name) ?? new List<WatchmanEvent>();
    }

    public void Push(WatchmanEvent e)
    {
        lock (_lock)
        {
            var q = All();
            q.Add(e);
            if (q.Count > MaxQueue) q.RemoveRange(0, q.Count - MaxQueue);
            store.WriteJson(Name, q);
        }
    }

    public int Count => All().Count;

    /// <summary>Sends one batch; keeps what the server did not take. Returns ids the server wants images for.</summary>
    public async Task<List<string>> FlushAsync(Func<List<WatchmanEvent>, Task<EventsResponse>> send)
    {
        List<WatchmanEvent> batch;
        lock (_lock) batch = All().Take(MaxBatch).ToList();
        if (batch.Count == 0) return new List<string>();
        var r = await send(batch);
        // Rejected events are malformed; sending them again would fail again.
        var done = new HashSet<string>(r.Accepted.Concat(r.Duplicates)
            .Concat((r.Rejected ?? new List<RejectedEvent>()).Select(x => x.Id).OfType<string>()));
        lock (_lock) store.WriteJson(Name, All().Where(e => !done.Contains(e.Id)).ToList());
        return r.Upload;
    }
}
