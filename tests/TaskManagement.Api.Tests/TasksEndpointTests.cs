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
    public async Task Health_ReturnsHealthy()
    {
        var client = factory.CreateClient();

        var response = await client.GetAsync("/health");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

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

    [Fact]
    public async Task GetAll_WithDueDateFilters_UsesExclusiveBoundsAndExcludesTasksWithoutDueDate()
    {
        await SeedTasksAsync(
            new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "Before cutoff",
                DueDate = new DateTime(1999, 12, 31),
                CreatedAt = DateTime.UtcNow
            },
            new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "At cutoff",
                DueDate = new DateTime(2000, 1, 1),
                CreatedAt = DateTime.UtcNow
            },
            new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "After cutoff",
                DueDate = new DateTime(2000, 1, 2),
                CreatedAt = DateTime.UtcNow
            },
            new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "No due date",
                DueDate = null,
                CreatedAt = DateTime.UtcNow
            },
            new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "After future cutoff",
                DueDate = new DateTime(2100, 1, 2),
                CreatedAt = DateTime.UtcNow
            });

        var client = factory.CreateClient();
        var beforeResponse = await client.GetAsync("/api/tasks?dueBefore=2000-01-01T00:00:00Z");
        var beforeTasks = await beforeResponse.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();
        var afterResponse = await client.GetAsync("/api/tasks?dueAfter=2099-01-01T00:00:00Z");
        var afterTasks = await afterResponse.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();

        beforeResponse.StatusCode.Should().Be(HttpStatusCode.OK);
        beforeTasks!.Select(task => task.Title).Should().Contain("Before cutoff");
        beforeTasks!.Select(task => task.Title).Should().NotContain("At cutoff", "After cutoff", "No due date");
        afterResponse.StatusCode.Should().Be(HttpStatusCode.OK);
        afterTasks!.Select(task => task.Title).Should().Contain("After future cutoff");
        afterTasks!.Select(task => task.Title).Should().NotContain("At cutoff", "No due date");
    }

    [Fact]
    public async Task GetAll_WithStatusAndDateRange_AppliesAllFiltersAndReturnsEmptyForImpossibleRange()
    {
        await SeedTasksAsync(
            new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "Matching range and status",
                Status = TaskItemStatus.Todo,
                DueDate = new DateTime(2102, 1, 1),
                CreatedAt = DateTime.UtcNow
            },
            new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "At lower bound",
                Status = TaskItemStatus.Todo,
                DueDate = new DateTime(2101, 1, 1),
                CreatedAt = DateTime.UtcNow
            },
            new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "At upper bound",
                Status = TaskItemStatus.Todo,
                DueDate = new DateTime(2103, 1, 1),
                CreatedAt = DateTime.UtcNow
            },
            new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "Wrong status",
                Status = TaskItemStatus.Done,
                DueDate = new DateTime(2102, 1, 1),
                CreatedAt = DateTime.UtcNow
            },
            new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "No due date",
                Status = TaskItemStatus.Todo,
                DueDate = null,
                CreatedAt = DateTime.UtcNow
            });

        var client = factory.CreateClient();
        var response = await client.GetAsync(
            "/api/tasks?status=Todo&dueAfter=2101-01-01T00:00:00Z&dueBefore=2103-01-01T00:00:00Z");
        var tasks = await response.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();
        var impossibleRangeResponse = await client.GetAsync(
            "/api/tasks?dueAfter=2102-01-01T00:00:00Z&dueBefore=2102-01-01T00:00:00Z");
        var impossibleRangeTasks =
            await impossibleRangeResponse.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();
        var invertedRangeResponse = await client.GetAsync(
            "/api/tasks?dueAfter=2103-01-01T00:00:00Z&dueBefore=2102-01-01T00:00:00Z");
        var invertedRangeTasks =
            await invertedRangeResponse.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        tasks.Should().ContainSingle(task => task.Title == "Matching range and status");
        impossibleRangeResponse.StatusCode.Should().Be(HttpStatusCode.OK);
        impossibleRangeTasks.Should().BeEmpty();
        invertedRangeResponse.StatusCode.Should().Be(HttpStatusCode.OK);
        invertedRangeTasks.Should().BeEmpty();
    }

    [Fact]
    public async Task GetAll_WithPriorityFilter_CombinesFiltersAndRejectsInvalidValues()
    {
        var highPriorityTask = new TaskItem
        {
            Id = Guid.NewGuid(),
            Title = "High priority in progress",
            Status = TaskItemStatus.InProgress,
            Priority = TaskPriority.High,
            DueDate = new DateTime(2220, 6, 1),
            CreatedAt = DateTime.UtcNow
        };
        var mediumPriorityTask = new TaskItem
        {
            Id = Guid.NewGuid(),
            Title = "Medium priority todo",
            Status = TaskItemStatus.Todo,
            Priority = TaskPriority.Medium,
            DueDate = new DateTime(2220, 6, 2),
            CreatedAt = DateTime.UtcNow.AddMinutes(-1)
        };
        var lowPriorityTask = new TaskItem
        {
            Id = Guid.NewGuid(),
            Title = "Low priority in progress",
            Status = TaskItemStatus.InProgress,
            Priority = TaskPriority.Low,
            DueDate = new DateTime(2220, 6, 3),
            CreatedAt = DateTime.UtcNow.AddMinutes(-2)
        };
        await SeedTasksAsync(highPriorityTask, mediumPriorityTask, lowPriorityTask);

        var client = factory.CreateClient();
        var allResponse = await client.GetAsync("/api/tasks");
        var allTasks = await allResponse.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();
        var highResponse = await client.GetAsync("/api/tasks?priority=High");
        var highTasks = await highResponse.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();
        var mediumResponse = await client.GetAsync("/api/tasks?priority=Medium");
        var mediumTasks = await mediumResponse.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();
        var lowResponse = await client.GetAsync("/api/tasks?priority=low");
        var lowTasks = await lowResponse.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();
        var combinedResponse = await client.GetAsync(
            "/api/tasks?status=InProgress&priority=High&dueAfter=2220-01-01T00:00:00Z&dueBefore=2221-01-01T00:00:00Z");
        var combinedTasks = await combinedResponse.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();
        var noMatchesResponse = await client.GetAsync("/api/tasks?status=Done&priority=Low");
        var noMatchesTasks = await noMatchesResponse.Content.ReadFromJsonAsync<IReadOnlyList<TaskResponse>>();

        allResponse.StatusCode.Should().Be(HttpStatusCode.OK);
        allTasks!.Select(task => task.Id).Should().Contain(
            [highPriorityTask.Id, mediumPriorityTask.Id, lowPriorityTask.Id]);
        highTasks!.Select(task => task.Id).Should().Contain(highPriorityTask.Id)
            .And.NotContain([mediumPriorityTask.Id, lowPriorityTask.Id]);
        mediumTasks!.Select(task => task.Id).Should().Contain(mediumPriorityTask.Id)
            .And.NotContain([highPriorityTask.Id, lowPriorityTask.Id]);
        lowTasks!.Select(task => task.Id).Should().Contain(lowPriorityTask.Id)
            .And.NotContain([highPriorityTask.Id, mediumPriorityTask.Id]);
        combinedResponse.StatusCode.Should().Be(HttpStatusCode.OK);
        combinedTasks!.Select(task => task.Id).Should().ContainSingle().Which.Should().Be(highPriorityTask.Id);
        noMatchesResponse.StatusCode.Should().Be(HttpStatusCode.OK);
        noMatchesTasks.Should().BeEmpty();

        (await client.GetAsync("/api/tasks?priority=Urgent")).StatusCode.Should().Be(HttpStatusCode.BadRequest);
        (await client.GetAsync("/api/tasks?priority=99")).StatusCode.Should().Be(HttpStatusCode.BadRequest);
        (await client.GetAsync("/api/tasks?priority=1")).StatusCode.Should().Be(HttpStatusCode.BadRequest);
        (await client.GetAsync("/api/tasks?priority=")).StatusCode.Should().Be(HttpStatusCode.BadRequest);
        (await client.GetAsync("/api/tasks?priority=Low&priority=High")).StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task GetAll_WithMalformedDueDate_ReturnsBadRequest()
    {
        var client = factory.CreateClient();

        var response = await client.GetAsync("/api/tasks?dueBefore=not-a-date");

        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    private async Task SeedTasksAsync(params TaskItem[] tasks)
    {
        await using var scope = factory.Services.CreateAsyncScope();
        var database = scope.ServiceProvider.GetRequiredService<TaskManagementDbContext>();
        database.Tasks.AddRange(tasks);
        await database.SaveChangesAsync();
    }
}
