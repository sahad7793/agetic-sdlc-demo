using Microsoft.EntityFrameworkCore;
using TaskManagement.Api.Data;
using TaskManagement.Api.Domain;

namespace TaskManagement.Api.Repositories;

public class EfTaskRepository(TaskManagementDbContext database) : ITaskRepository
{
    public async Task<IReadOnlyList<TaskItem>> GetAllAsync(TaskItemStatus? status, CancellationToken cancellationToken)
    {
        IQueryable<TaskItem> query = database.Tasks.AsNoTracking().OrderByDescending(task => task.CreatedAt);
        if (status is not null)
        {
            query = query.Where(task => task.Status == status);
        }

        return await query.ToListAsync(cancellationToken);
    }

    public async Task<IReadOnlyList<TaskItem>> GetWithDueDateBeforeAsync(DateTime dueBefore, CancellationToken cancellationToken) =>
        await database.Tasks
            .AsNoTracking()
            .Where(task => task.DueDate.HasValue && task.DueDate.Value < dueBefore)
            .OrderByDescending(task => task.CreatedAt)
            .ToListAsync(cancellationToken);

    public Task<TaskItem?> GetByIdAsync(Guid id, CancellationToken cancellationToken) =>
        database.Tasks.SingleOrDefaultAsync(task => task.Id == id, cancellationToken);

    public async Task AddAsync(TaskItem task, CancellationToken cancellationToken)
    {
        await database.Tasks.AddAsync(task, cancellationToken);
        await database.SaveChangesAsync(cancellationToken);
    }

    public async Task UpdateAsync(TaskItem task, CancellationToken cancellationToken)
    {
        database.Tasks.Update(task);
        await database.SaveChangesAsync(cancellationToken);
    }

    public async Task DeleteAsync(TaskItem task, CancellationToken cancellationToken)
    {
        database.Tasks.Remove(task);
        await database.SaveChangesAsync(cancellationToken);
    }
}
