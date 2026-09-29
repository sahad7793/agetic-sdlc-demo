using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace TaskManagement.Api.Data;

public sealed class TaskManagementDesignTimeDbContextFactory
    : IDesignTimeDbContextFactory<TaskManagementDbContext>
{
    public TaskManagementDbContext CreateDbContext(string[] args)
    {
        var options = new DbContextOptionsBuilder<TaskManagementDbContext>()
            .UseSqlServer(
                "Server=127.0.0.1,1;Database=TaskManagementDesignTime;Integrated Security=True;TrustServerCertificate=True;Connect Timeout=1")
            .Options;

        return new TaskManagementDbContext(options);
    }
}
