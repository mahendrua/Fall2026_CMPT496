import { test, expect } from '@playwright/test';

test.describe('Editing Todos', () => {
  const BASE_URL = process.env.BASE_URL || 'http://localhost:3000';

  test.beforeEach(async ({ page }) => {
    page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
    page.on('console', (msg) => {
      if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
    });
    await page.goto(BASE_URL);
  });

  test('should support editing, canceling, and deletion via empty edit', async ({ page }) => {
    test.info().annotations.push(
      { type: 'purpose', description: 'Verify handleEdit and updateItem logic in src/Item.svelte.' },
      { type: 'expected', description: 'Changes save on Enter/Blur, revert on Escape, and item is removed if cleared then blurred.' }
    );

    const input = page.getByPlaceholder('What needs to be done?');
    await input.fill('Initial Task');
    await input.press('Enter');

    const todoItem = page.locator('.todo-list li').first();
    const label = todoItem.locator('label');

    // Start editing (src/Item.svelte:40)
    await label.dblclick();
    const editInput = todoItem.getByLabel('Edit todo');
    await expect(editInput).toBeFocused();

    // Edit and cancel with Escape
    await editInput.fill('Changed text');
    await editInput.press('Escape');
    await expect(label).toHaveText('Initial Task');

    // Edit and save with Enter
    await label.dblclick();
    await editInput.fill('Final Task');
    await editInput.press('Enter');
    await expect(label).toHaveText('Final Task');

    // Remove by clearing and blurring (src/Item.svelte:28)
    await label.dblclick();
    await editInput.fill('');
    await editInput.blur();
    await expect(page.locator('.todo-list li')).toHaveCount(0);
  });
});
