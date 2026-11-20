import { test, expect } from '@playwright/test';

test.describe('Filtering and Bulk Actions', () => {
  const BASE_URL = process.env.BASE_URL || 'http://localhost:3000';

  test.beforeEach(async ({ page }) => {
    page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
    page.on('console', (msg) => {
      if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
    });
    await page.goto(BASE_URL);
  });

  test('should filter todos and perform bulk actions', async ({ page }) => {
    test.info().annotations.push(
      { type: 'purpose', description: 'Verify router logic in src/router.js and bulk handlers in src/App.svelte.' },
      { type: 'expected', description: 'Filtering changes visible list; toggle-all affects all items; clear completed removes completed only.' }
    );

    const input = page.getByPlaceholder('What needs to be done?');
    await input.fill('Task 1');
    await input.press('Enter');
    await input.fill('Task 2');
    await input.press('Enter');

    const todoList = page.locator('.todo-list li');
    
    // Toggle all (src/App.svelte:58)
    await page.getByLabel('Mark all as complete').click();
    await expect(todoList.filter({ hasClass: 'completed' })).toHaveCount(2);

    // Un-toggle one for filter testing
    await todoList.first().locator('.toggle').uncheck();

    // Filter Active (src/Footer.svelte:13)
    await page.getByRole('link', { name: 'Active' }).click();
    await expect(todoList).toHaveCount(1);
    await expect(todoList).toContainText('Task 1');

    // Filter Completed (src/Footer.svelte:14)
    await page.getByRole('link', { name: 'Completed' }).click();
    await expect(todoList).toHaveCount(1);
    await expect(todoList).toContainText('Task 2');

    // Clear Completed (src/Footer.svelte:17)
    await page.getByRole('button', { name: 'Clear completed' }).click();
    await page.getByRole('link', { name: 'All' }).click();
    await expect(todoList).toHaveCount(1);
    await expect(todoList).toContainText('Task 1');
  });
});
