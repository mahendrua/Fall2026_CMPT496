import { test, expect } from '@playwright/test';

test.describe('Todo Lifecycle', () => {
  const BASE_URL = process.env.BASE_URL || 'http://localhost:3000';

  test.beforeEach(async ({ page }) => {
    page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
    page.on('console', (msg) => {
      if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
    });
    await page.goto(BASE_URL);
  });

  test('should add, toggle, and delete a todo item', async ({ page }) => {
    test.info().annotations.push(
      { type: 'purpose', description: 'Test creation, completion state, and removal of a todo item based on src/Header.svelte and src/Item.svelte.' },
      { type: 'expected', description: 'Item count updates in footer, item gets .completed class when checked, and is removed from DOM on delete click.' }
    );

    const input = page.getByPlaceholder('What needs to be done?');
    const todoList = page.locator('.todo-list');
    const todoCount = page.locator('.todo-count');

    // Add items
    await input.fill('Buy groceries');
    await input.press('Enter');
    await input.fill('Walk the dog');
    await input.press('Enter');

    await expect(todoList.locator('li')).toHaveCount(2);
    await expect(todoCount).toHaveText('2 items left');

    // Toggle completion (src/Item.svelte:38)
    const firstTodo = todoList.locator('li').filter({ hasText: 'Buy groceries' });
    await firstTodo.locator('.toggle').check();
    await expect(firstTodo).toHaveClass(/completed/);
    await expect(todoCount).toHaveText('1 item left');

    // Delete item (src/Item.svelte:41)
    // Note: The delete button (.destroy) is often visible only on hover in TodoMVC styles
    await firstTodo.hover();
    await firstTodo.getByLabel('Delete').click();

    await expect(todoList.locator('li')).toHaveCount(1);
    await expect(todoList).toContainText('Walk the dog');
    await expect(todoCount).toHaveText('1 item left');
  });
});
