using System.IO.Pipes;
using Watchman.Service;
using Xunit;

namespace Watchman.Core.Tests;

public class PipeTests
{
    [Fact]
    public async Task TheServerKnowsWhichProcessConnected()
    {
        var name = "watchman-test-" + Guid.NewGuid().ToString("N");
        using var server = new NamedPipeServerStream(name, PipeDirection.InOut, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous);
        using var client = new NamedPipeClientStream(".", name, PipeDirection.InOut, PipeOptions.Asynchronous);
        var accept = server.WaitForConnectionAsync();
        await client.ConnectAsync(5000);
        await accept;
        Assert.Equal(Environment.ProcessId, PipeServer.ClientPid(server));
    }
}
