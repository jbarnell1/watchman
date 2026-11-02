using System.IO.Pipes;
using Watchman.Core;

namespace Watchman.Helper;

/// <summary>The helper's connection to the service. Reconnects forever; the service restarts us if we die.</summary>
public sealed class PipeClient : IDisposable
{
    private NamedPipeClientStream? _pipe;
    private StreamWriter? _writer;
    private readonly CancellationTokenSource _cts = new();
    private readonly SemaphoreSlim _gate = new(1, 1);

    public event Action<ConfigMessage>? ConfigReceived;
    public event Action<ResultMessage>? ResultReceived;
    public event Action<bool>? Connected;
    public bool IsConnected => _pipe?.IsConnected == true;

    public void Start() => _ = Task.Run(LoopAsync);

    private async Task LoopAsync()
    {
        while (!_cts.IsCancellationRequested)
        {
            try
            {
                using var pipe = new NamedPipeClientStream(".", Pipe.Name, PipeDirection.InOut, PipeOptions.Asynchronous);
                await pipe.ConnectAsync(5000, _cts.Token);
                _pipe = pipe;
                _writer = new StreamWriter(pipe) { AutoFlush = true };
                Connected?.Invoke(true);
                await SendAsync(new HelloMessage("0.1.0", Environment.UserName));
                using var reader = new StreamReader(pipe);
                while (pipe.IsConnected && !_cts.IsCancellationRequested)
                {
                    var line = await reader.ReadLineAsync(_cts.Token);
                    if (line is null) break;
                    switch (Wire.Decode<ServiceMessage>(line))
                    {
                        case ConfigMessage c: ConfigReceived?.Invoke(c); break;
                        case ResultMessage r: ResultReceived?.Invoke(r); break;
                    }
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception) { await Task.Delay(3000); }
            finally
            {
                _writer = null;
                _pipe = null;
                Connected?.Invoke(false);
            }
            await Task.Delay(2000);
        }
    }

    public async Task SendAsync(HelperMessage msg)
    {
        var w = _writer;
        if (w is null) return;
        await _gate.WaitAsync();
        try { await w.WriteLineAsync(Wire.Encode(msg)); }
        catch (Exception) { }
        finally { _gate.Release(); }
    }

    public void Dispose()
    {
        _cts.Cancel();
        _pipe?.Dispose();
    }
}
