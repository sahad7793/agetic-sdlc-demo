using TaskManagement.Api.Domain;

namespace TaskManagement.Api.Repositories;

public interface ITaskRepository
{
    Task<IReadOnlyList<TaskItem>> GetAllAsync(TaskItemStatus? status, CancellationToken cancellationToken);
    Task<IReadOnlyList<TaskItem>> GetWithDueDateBeforeAsync(DateTime dueBefore, CancellationToken cancellationToken);
    Task<TaskItem?> GetByIdAsync(Guid id, CancellationToken cancellationToken);
    Task AddAsync(TaskItem task, CancellationToken cancellationToken);
    Task UpdateAsync(TaskItem task, CancellationToken cancellationToken);
    Task DeleteAsync(TaskItem task, CancellationToken cancellationToken);
}
