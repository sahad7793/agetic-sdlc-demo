using System.ComponentModel.DataAnnotations;
using TaskManagement.Api.Domain;

namespace TaskManagement.Api.DTOs;

public class CreateTaskRequest
{
    [Required]
    [StringLength(200, MinimumLength = 1)]
    public required string Title { get; init; }

    [StringLength(2_000)]
    public string? Description { get; init; }

    [EnumDataType(typeof(TaskPriority))]
    public TaskPriority Priority { get; init; } = TaskPriority.Medium;

    public DateTime? DueDate { get; init; }
}
