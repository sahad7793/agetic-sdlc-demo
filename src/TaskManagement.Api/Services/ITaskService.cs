using TaskManagement.Api.Domain;
using TaskManagement.Api.DTOs;

namespace TaskManagement.Api.Services;

public interface ITaskService
{
    Task<IReadOnlyList<TaskResponse>> GetAllAsync(TaskItemStatus? status, CancellationToken cancellationToken);
    Task<TaskResponse?> GetByIdAsync(Guid id, CancellationToken cancellationToken);
    Task<TaskResponse> CreateAsync(CreateTaskRequest request, CancellationToken cancellationToken);
    Task<TaskResponse?> UpdateAsync(Guid id, UpdateTaskRequest request, CancellationToken cancellationToken);
    Task<TaskResponse?> UpdateStatusAsync(Guid id, UpdateTaskStatusRequest request, CancellationToken cancellationToken);
    Task<bool> DeleteAsync(Guid id, CancellationToken cancellationToken);
}
