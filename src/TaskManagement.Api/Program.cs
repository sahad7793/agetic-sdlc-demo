using Microsoft.EntityFrameworkCore;
using TaskManagement.Api.Data;
using TaskManagement.Api.Repositories;
using TaskManagement.Api.Services;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var useAzureSql = builder.Configuration.GetValue<bool>("Database:UseAzureSql");
var connectionString = builder.Configuration.GetConnectionString("TaskManagement")
    ?? throw new InvalidOperationException("The TaskManagement connection string is required.");

builder.Services.AddDbContext<TaskManagementDbContext>(options =>
{
    if (useAzureSql)
    {
        options.UseSqlServer(connectionString, sqlServerOptions =>
            sqlServerOptions.EnableRetryOnFailure());
        return;
    }

    options.UseSqlite(connectionString);
});
builder.Services.AddScoped<ITaskRepository, EfTaskRepository>();
builder.Services.AddScoped<ITaskService, TaskService>();
builder.Services.AddHealthChecks();

var app = builder.Build();

using (var scope = app.Services.CreateScope())
{
    var database = scope.ServiceProvider.GetRequiredService<TaskManagementDbContext>();
    if (useAzureSql)
    {
        await database.Database.MigrateAsync();
    }
    else
    {
        await database.Database.EnsureCreatedAsync();
    }
}

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseHttpsRedirection();
app.MapHealthChecks("/health");
app.MapControllers();
app.Run();

public partial class Program;
