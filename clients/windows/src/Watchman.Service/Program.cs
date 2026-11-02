// The Watchman Windows service (runs as SYSTEM). Also a small command line for pairing:
//   WatchmanService.exe pair --server https://watchman.tailnet.ts.net --code K7Q-4MZ-P2D
//   WatchmanService.exe status

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Watchman.Core;
using Watchman.Service;

if (args.Length > 0 && args[0] is "pair" or "status" or "unpair")
{
    return await Cli.RunAsync(args);
}

var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddWindowsService(o => o.ServiceName = "Watchman");
builder.Services.AddSingleton<IClock, SystemClock>();
builder.Services.AddSingleton<IStore>(_ => new ProtectedFileStore(Paths.DataDir));
builder.Services.AddSingleton<ServiceState>();
builder.Services.AddSingleton<PipeServer>();
builder.Services.AddHostedService(sp => sp.GetRequiredService<PipeServer>());
builder.Services.AddHostedService<ServiceWorker>();
builder.Services.AddHostedService<HelperLauncher>();
await builder.Build().RunAsync();
return 0;
