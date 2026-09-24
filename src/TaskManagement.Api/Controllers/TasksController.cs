using Microsoft.AspNetCore.Mvc;
using TaskManagement.Api.Domain;
using TaskManagement.Api.DTOs;
using TaskManagement.Api.Services;

namespace TaskManagement.Api.Controllers;

[ApiController]
[Route("api/tasks")]
public class TasksController(ITaskService service, ILogger<TasksController> logger) : ControllerBase
{
    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<TaskResponse>), StatusCodes.Status200OK)]
    public async Task<ActionResult<IReadOnlyList<TaskResponse>>> GetAll(
        [FromQuery] TaskItemStatus? status,
        CancellationToken cancellationToken)
    {
        return Ok(await service.GetAllAsync(status, cancellationToken));
    }

    [HttpGet("overdue")]
    [ProducesResponseType(typeof(IReadOnlyList<TaskResponse>), StatusCodes.Status200OK)]
    public async Task<ActionResult<IReadOnlyList<TaskResponse>>> GetOverdue(CancellationToken cancellationToken)
    {
        return Ok(await service.GetOverdueAsync(cancellationToken));
    }

    [HttpGet("{id:guid}")]
    [ProducesResponseType(typeof(TaskResponse), StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<ActionResult<TaskResponse>> GetById(Guid id, CancellationToken cancellationToken)
    {
        var task = await service.GetByIdAsync(id, cancellationToken);
        return task is null ? NotFound() : Ok(task);
    }

    [HttpPost]
    [ProducesResponseType(typeof(TaskResponse), StatusCodes.Status201Created)]
    [ProducesResponseType(typeof(ValidationProblemDetails), StatusCodes.Status400BadRequest)]
    public async Task<ActionResult<TaskResponse>> Create(CreateTaskRequest request, CancellationToken cancellationToken)
    {
        try
        {
            var task = await service.CreateAsync(request, cancellationToken);
            return CreatedAtAction(nameof(GetById), new { task.Id }, task);
        }
        catch (BusinessRuleViolationException exception)
        {
            logger.LogWarning(exception, "Task creation rejected");
            return ValidationProblem(detail: exception.Message);
        }
    }

    [HttpPut("{id:guid}")]
    [ProducesResponseType(typeof(TaskResponse), StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<ActionResult<TaskResponse>> Update(Guid id, UpdateTaskRequest request, CancellationToken cancellationToken)
    {
        try
        {
            var task = await service.UpdateAsync(id, request, cancellationToken);
            return task is null ? NotFound() : Ok(task);
        }
        catch (BusinessRuleViolationException exception)
        {
            logger.LogWarning(exception, "Task update rejected for {TaskId}", id);
            return ValidationProblem(detail: exception.Message);
        }
    }

    [HttpPatch("{id:guid}/status")]
    [ProducesResponseType(typeof(TaskResponse), StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<ActionResult<TaskResponse>> UpdateStatus(
        Guid id,
        UpdateTaskStatusRequest request,
        CancellationToken cancellationToken)
    {
        try
        {
            var task = await service.UpdateStatusAsync(id, request, cancellationToken);
            return task is null ? NotFound() : Ok(task);
        }
        catch (BusinessRuleViolationException exception)
        {
            logger.LogWarning(exception, "Task status update rejected for {TaskId}", id);
            return ValidationProblem(detail: exception.Message);
        }
    }

    [HttpDelete("{id:guid}")]
    [ProducesResponseType(StatusCodes.Status204NoContent)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<IActionResult> Delete(Guid id, CancellationToken cancellationToken) =>
        await service.DeleteAsync(id, cancellationToken) ? NoContent() : NotFound();
}
