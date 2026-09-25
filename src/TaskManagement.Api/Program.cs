using Azure.Monitor.OpenTelemetry.AspNetCore;
using Microsoft.EntityFrameworkCore;
using TaskManagement.Api.Data;
using TaskManagement.Api.Repositories;
using TaskManagement.Api.Services;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

// Wires the Application Insights connection string (already provided as an env var by
// infra/environment.bicep) to the OpenTelemetry distro so request and SQL dependency
// telemetry actually flow to Application Insights for the observability alerts/workbook.
if (!string.IsNullOrEmpty(builder.Configuration["APPLICATIONINSIGHTS_CONNECTION_STRING"]))
{
    builder.Services.AddOpenTelemetry().UseAzureMonitor();
}

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
