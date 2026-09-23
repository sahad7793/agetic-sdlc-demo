using System.ComponentModel.DataAnnotations;
using TaskManagement.Api.Domain;

namespace TaskManagement.Api.DTOs;

public class UpdateTaskStatusRequest
{
    [EnumDataType(typeof(TaskItemStatus))]
    public TaskItemStatus Status { get; init; }
}
