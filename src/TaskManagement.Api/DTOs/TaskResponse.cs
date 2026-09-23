using TaskManagement.Api.Domain;

namespace TaskManagement.Api.DTOs;

public record TaskResponse(
    Guid Id,
    string Title,
    string? Description,
    TaskItemStatus Status,
    TaskPriority Priority,
    DateTime? DueDate,
    DateTime CreatedAt);
