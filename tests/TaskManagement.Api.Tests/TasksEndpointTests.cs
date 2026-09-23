using System.Net;
using System.Net.Http.Json;
using FluentAssertions;
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
}
