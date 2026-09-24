using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using TaskManagement.Api.Domain;
using TaskManagement.Api.DTOs;
using TaskManagement.Api.Repositories;
using TaskManagement.Api.Services;

namespace TaskManagement.Api.Tests;

public class TaskServiceTests
{
    [Fact]
    public async Task CreateAsync_RejectsPastDueDate()
    {
        var service = CreateService();

        var action = () => service.CreateAsync(
            new CreateTaskRequest { Title = "Past task", DueDate = DateTime.UtcNow.AddDays(-1) },
            CancellationToken.None);

        await action.Should().ThrowAsync<BusinessRuleViolationException>()
            .WithMessage("*DueDate cannot be in the past*");
    }

    [Fact]
    public async Task UpdateStatusAsync_RejectsTodoToDoneTransition()
    {
        var repository = new FakeTaskRepository
        {
            Task = new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "Task",
                Status = TaskItemStatus.Todo,
                CreatedAt = DateTime.UtcNow
            }
        };
        var service = new TaskService(repository, NullLogger<TaskService>.Instance);

        var action = () => service.UpdateStatusAsync(
            repository.Task.Id,
            new UpdateTaskStatusRequest { Status = TaskItemStatus.Done },
            CancellationToken.None);

        await action.Should().ThrowAsync<BusinessRuleViolationException>()
            .WithMessage("*Todo to Done*");
    }

    [Fact]
    public async Task UpdateStatusAsync_AllowsSequentialTransitions()
    {
        var repository = new FakeTaskRepository
        {
            Task = new TaskItem
            {
                Id = Guid.NewGuid(),
                Title = "Task",
                Status = TaskItemStatus.Todo,
                CreatedAt = DateTime.UtcNow
            }
        };
        var service = new TaskService(repository, NullLogger<TaskService>.Instance);

        var inProgress = await service.UpdateStatusAsync(
            repository.Task.Id,
            new UpdateTaskStatusRequest { Status = TaskItemStatus.InProgress },
            CancellationToken.None);
        var completed = await service.UpdateStatusAsync(
            repository.Task.Id,
            new UpdateTaskStatusRequest { Status = TaskItemStatus.Done },
            CancellationToken.None);

        inProgress!.Status.Should().Be(TaskItemStatus.InProgress);
        completed!.Status.Should().Be(TaskItemStatus.Done);
    }

    [Fact]
    public async Task GetOverdueAsync_IncludesOverdueNotDoneTasks()
    {
        var overdueTask = new TaskItem
        {
            Id = Guid.NewGuid(),
            Title = "Overdue",
            Status = TaskItemStatus.InProgress,
            DueDate = DateTime.UtcNow.AddDays(-1),
            CreatedAt = DateTime.UtcNow
        };
        var repository = new FakeTaskRepository { Tasks = [overdueTask] };
        var service = new TaskService(repository, NullLogger<TaskService>.Instance);

        var result = await service.GetOverdueAsync(CancellationToken.None);

        result.Should().ContainSingle(task => task.Id == overdueTask.Id);
    }

    [Fact]
    public async Task GetOverdueAsync_ExcludesDoneTasksEvenWhenOverdue()
    {
        var doneOverdueTask = new TaskItem
        {
            Id = Guid.NewGuid(),
            Title = "Done",
            Status = TaskItemStatus.Done,
            DueDate = DateTime.UtcNow.AddDays(-1),
            CreatedAt = DateTime.UtcNow
        };
        var repository = new FakeTaskRepository { Tasks = [doneOverdueTask] };
        var service = new TaskService(repository, NullLogger<TaskService>.Instance);

        var result = await service.GetOverdueAsync(CancellationToken.None);

        result.Should().BeEmpty();
    }

    [Fact]
    public async Task GetOverdueAsync_ExcludesTasksThatAreNotYetDue()
    {
        var notYetDueTask = new TaskItem
        {
            Id = Guid.NewGuid(),
            Title = "Future",
            Status = TaskItemStatus.Todo,
            DueDate = DateTime.UtcNow.AddDays(1),
            CreatedAt = DateTime.UtcNow
        };
        var repository = new FakeTaskRepository { Tasks = [notYetDueTask] };
        var service = new TaskService(repository, NullLogger<TaskService>.Instance);

        var result = await service.GetOverdueAsync(CancellationToken.None);

        result.Should().BeEmpty();
    }

    private static TaskService CreateService() =>
        new(new FakeTaskRepository(), NullLogger<TaskService>.Instance);

    private sealed class FakeTaskRepository : ITaskRepository
    {
        public TaskItem? Task { get; set; }
        public List<TaskItem> Tasks { get; set; } = [];

        public Task<IReadOnlyList<TaskItem>> GetAllAsync(TaskItemStatus? status, CancellationToken cancellationToken) =>
            System.Threading.Tasks.Task.FromResult<IReadOnlyList<TaskItem>>(
                Task is null ? [] : [Task]);

        public Task<IReadOnlyList<TaskItem>> GetWithDueDateBeforeAsync(DateTime dueBefore, CancellationToken cancellationToken) =>
            System.Threading.Tasks.Task.FromResult<IReadOnlyList<TaskItem>>(
                Tasks.Where(task => task.DueDate.HasValue && task.DueDate.Value < dueBefore).ToList());

        public Task<TaskItem?> GetByIdAsync(Guid id, CancellationToken cancellationToken) =>
            System.Threading.Tasks.Task.FromResult(Task?.Id == id ? Task : null);

        public Task AddAsync(TaskItem task, CancellationToken cancellationToken)
        {
            Task = task;
            return System.Threading.Tasks.Task.CompletedTask;
        }

        public Task UpdateAsync(TaskItem task, CancellationToken cancellationToken) =>
            System.Threading.Tasks.Task.CompletedTask;

        public Task DeleteAsync(TaskItem task, CancellationToken cancellationToken)
        {
            Task = null;
            return System.Threading.Tasks.Task.CompletedTask;
        }
    }
}
