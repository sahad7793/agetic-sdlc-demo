using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
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
            // EF Core registers each AddDbContext call's provider configuration as an
            // IDbContextOptionsConfiguration<TContext> entry that is chained together when
            // DbContextOptions are built. Removing only the DbContextOptions<TContext>
            // descriptor leaves Program.cs's Sqlite configuration registered, so it gets
            // combined with the InMemory configuration below and EF Core throws because two
            // providers are registered. Both descriptor kinds must be removed.
            var efCoreDescriptors = services
                .Where(descriptor =>
                    descriptor.ServiceType == typeof(DbContextOptions<TaskManagementDbContext>) ||
                    descriptor.ServiceType == typeof(IDbContextOptionsConfiguration<TaskManagementDbContext>))
                .ToList();

            foreach (var descriptor in efCoreDescriptors)
            {
                services.Remove(descriptor);
            }

            services.AddDbContext<TaskManagementDbContext>(options =>
                options.UseInMemoryDatabase(databaseName));
        });
    }
}
