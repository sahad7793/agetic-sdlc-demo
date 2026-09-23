using TaskManagement.Api.Domain;
using TaskManagement.Api.DTOs;
using TaskManagement.Api.Repositories;

namespace TaskManagement.Api.Services;

public class TaskService(ITaskRepository repository, ILogger<TaskService> logger) : ITaskService
{
    public async Task<IReadOnlyList<TaskResponse>> GetAllAsync(TaskItemStatus? status, CancellationToken cancellationToken)
    {
        var tasks = await repository.GetAllAsync(status, cancellationToken);
        return tasks.Select(Map).ToList();
    }

    public async Task<TaskResponse?> GetByIdAsync(Guid id, CancellationToken cancellationToken)
    {
        var task = await repository.GetByIdAsync(id, cancellationToken);
        return task is null ? null : Map(task);
    }

    public async Task<TaskResponse> CreateAsync(CreateTaskRequest request, CancellationToken cancellationToken)
    {
        EnsureDueDateIsNotPast(request.DueDate);

        var task = new TaskItem
        {
            Id = Guid.NewGuid(),
            Title = request.Title.Trim(),
            Description = request.Description?.Trim(),
            Priority = request.Priority,
            DueDate = request.DueDate,
            CreatedAt = DateTime.UtcNow
        };

        await repository.AddAsync(task, cancellationToken);
        logger.LogInformation("Created task {TaskId} with priority {Priority}", task.Id, task.Priority);
        return Map(task);
    }

    public async Task<TaskResponse?> UpdateAsync(Guid id, UpdateTaskRequest request, CancellationToken cancellationToken)
    {
        EnsureDueDateIsNotPast(request.DueDate);
        var task = await repository.GetByIdAsync(id, cancellationToken);
        if (task is null)
        {
            return null;
        }

        task.Title = request.Title.Trim();
        task.Description = request.Description?.Trim();
        task.Priority = request.Priority;
        task.DueDate = request.DueDate;
        await repository.UpdateAsync(task, cancellationToken);
        logger.LogInformation("Updated task {TaskId}", task.Id);
        return Map(task);
    }

    public async Task<TaskResponse?> UpdateStatusAsync(Guid id, UpdateTaskStatusRequest request, CancellationToken cancellationToken)
    {
        var task = await repository.GetByIdAsync(id, cancellationToken);
        if (task is null)
        {
            return null;
        }

        if (!IsValidTransition(task.Status, request.Status))
        {
            throw new BusinessRuleViolationException(
                $"Task status cannot transition from {task.Status} to {request.Status}. Tasks must move from Todo to InProgress to Done.");
        }

        task.Status = request.Status;
        await repository.UpdateAsync(task, cancellationToken);
        logger.LogInformation("Changed task {TaskId} status to {Status}", task.Id, task.Status);
        return Map(task);
    }

    public async Task<bool> DeleteAsync(Guid id, CancellationToken cancellationToken)
    {
        var task = await repository.GetByIdAsync(id, cancellationToken);
        if (task is null)
        {
            return false;
        }

        await repository.DeleteAsync(task, cancellationToken);
        logger.LogInformation("Deleted task {TaskId}", id);
        return true;
    }

    private static void EnsureDueDateIsNotPast(DateTime? dueDate)
    {
        if (dueDate?.Date < DateTime.UtcNow.Date)
        {
            throw new BusinessRuleViolationException("DueDate cannot be in the past.");
        }
    }

    private static bool IsValidTransition(TaskItemStatus current, TaskItemStatus requested) =>
        (current, requested) is
            (TaskItemStatus.Todo, TaskItemStatus.InProgress) or
            (TaskItemStatus.InProgress, TaskItemStatus.Done);

    private static TaskResponse Map(TaskItem task) =>
        new(task.Id, task.Title, task.Description, task.Status, task.Priority, task.DueDate, task.CreatedAt);
}
