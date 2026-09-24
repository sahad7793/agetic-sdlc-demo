using System.Net;
using System.Net.Http.Json;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;
using TaskManagement.Api.Data;
using TaskManagement.Api.Domain;
using TaskManagement.Api.DTOs;

namespace TaskManagement.Api.Tests;

public class TasksEndpointTests(TaskApiFactory factory) : IClassFixture<TaskApiFactory>
{
    [Fact]
    public async Task CreateThenGetById_ReturnsPersistedTask()
    {
        var client = factory.CreateClient();
        var create = new CreateTaskRequest
        {
            Title = "Prepare release",
            Description = "Run the final checks",
            DueDate = DateTime.UtcNow.AddDays(1)
        };

        var createResponse = await client.PostAsJsonAsync("/api/tasks", create);
        var created = await createResponse.Content.ReadFromJsonAsync<TaskResponse>();
        var getResponse = await client.GetAsync($"/api/tasks/{created!.Id}");

        createResponse.StatusCode.Should().Be(HttpStatusCode.Created);
        (await getResponse.Content.ReadFromJsonAsync<TaskResponse>())!.Title.Should().Be(create.Title);
    }

    [Fact]
    public async Task CreateWithPastDueDate_ReturnsBadRequest()
    {
        var client = factory.CreateClient();

        var response = await client.PostAsJsonAsync("/api/tasks", new CreateTaskRequest
        {
            Title = "Late task",
            DueDate = DateTime.UtcNow.AddDays(-1)
        });

        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task GetOverdue_ReturnsOnlyOverdueNotDoneTasks()
    {
        await using (var scope = factory.Services.CreateAsyncScope())
        {
            var database = scope.ServiceProvider.GetRequiredService<TaskManagementDbContext>();
            database.Tasks.AddRange(
                new TaskItem
                {
                    Id = Guid.NewGuid(),
                    Title = "Overdue todo",
                    Status = TaskItemStatus.Todo,
                    DueDate = DateTime.UtcNow.AddDays(-2),
                    CreatedAt = DateTime.UtcNow.AddHours(-2)
                },
                new TaskItem
                {
                    Id = Guid.NewGuid(),
                    Title = "Overdue done",
                    Status = TaskItemStatus.Done,
                    DueDate = DateTime.UtcNow.AddDays(-2),
                    CreatedAt = DateTime.UtcNow.AddHours(-3)
                },
                new TaskItem
                {
                    Id = Guid.NewGuid(),
                    Title = "No due date",
                    Status = TaskItemStatus.Todo,
                    DueDate = null,
                    CreatedAt = DateTime.UtcNow.AddHours(-1)
                },
                new TaskItem
                {
                    Id = Guid.NewGuid(),
                    Title = "Future task",
                    Status = TaskItemStatus.InProgress,
                    DueDate = DateTime.UtcNow.AddDays(2),
                    CreatedAt = DateTime.UtcNow
                });
            await database.SaveChangesAsync();
        }

        var client = factory.CreateClient();
        var response = await client.GetAsync("/api/tasks/overdue");
        var tasks = await response.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        tasks.Should().ContainSingle(task => task.Title == "Overdue todo");
    }
}
