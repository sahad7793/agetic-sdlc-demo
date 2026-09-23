using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using TaskManagement.Api.Data;

namespace TaskManagement.Api.Tests;

public sealed class TaskApiFactory : WebApplicationFactory<Program>
{
    private readonly string databaseName = $"TaskManagementTests-{Guid.NewGuid()}";

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseEnvironment("Testing");
        builder.ConfigureServices(services =>
        {
            var dbContextOptions = services
                .Where(descriptor => descriptor.ServiceType == typeof(DbContextOptions<TaskManagementDbContext>))
                .ToList();

            foreach (var descriptor in dbContextOptions)
            {
                services.Remove(descriptor);
            }

            services.AddDbContext<TaskManagementDbContext>(options =>
                options.UseInMemoryDatabase(databaseName));
        });
    }
}
