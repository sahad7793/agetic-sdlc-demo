namespace TaskManagement.Api.Services;

public class BusinessRuleViolationException(string message) : Exception(message);
