using Microsoft.EntityFrameworkCore;
using TaskManagement.Api.Domain;

namespace TaskManagement.Api.Data;

public class TaskManagementDbContext(DbContextOptions<TaskManagementDbContext> options) : DbContext(options)
{
    public DbSet<TaskItem> Tasks => Set<TaskItem>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<TaskItem>(entity =>
        {
            entity.HasKey(task => task.Id);
            entity.Property(task => task.Title).HasMaxLength(200).IsRequired();
            entity.Property(task => task.Description).HasMaxLength(2_000);
            entity.Property(task => task.Status).HasConversion<string>().HasMaxLength(20);
            entity.Property(task => task.Priority).HasConversion<string>().HasMaxLength(20);
        });
    }
}
