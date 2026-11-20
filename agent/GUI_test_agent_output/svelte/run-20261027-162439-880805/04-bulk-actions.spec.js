import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should toggle all items and clear completed', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Tests toggleAllItems and removeCompletedItems functions' },
    { type: 'expected', description: 'All items update completion state simultaneously and clear completed removes them' }
  );

  const mainInput = page.getByPlaceholder('What needs to be done?');
  await mainInput.fill('Task 1');
  await mainInput.press('Enter');
  await mainInput.fill('Task 2');
  await mainInput.press('Enter');

  const toggleAll = page.locator('#toggle-all');
  const todos = page.locator('.todo-list li');

  // Toggle All On
  await toggleAll.click();
  await expect(todos).toHaveClass([/completed/, /completed/]);
  await expect(page.locator('.todo-count')).toHaveText('0 items left');

  // Toggle All Off
  await toggleAll.click();
  await expect(todos).not.toHaveClass([/completed/, /completed/]);
  await expect(page.locator('.todo-count')).toHaveText('2 items left');

  // Clear Completed
  await todos.first().getByRole('checkbox').check();
  const clearBtn = page.getByRole('button', { name: 'Clear completed' });
  await expect(clearBtn).toBeVisible();
  await clearBtn.click();
  
  await expect(todos).toHaveCount(1);
  await expect(todos).toHaveText('Task 2');
  await expect(clearBtn).toBeHidden();
});
