import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should filter items correctly', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Check that filter links correctly update the visible items.' },
    { type: 'expected', description: 'Active and Completed filters show only appropriate items.' }
  );

  await page.getByPlaceholder('What needs to be done?').fill('Active Item');
  await page.getByPlaceholder('What needs to be done?').press('Enter');
  await page.getByPlaceholder('What needs to be done?').fill('Completed Item');
  await page.getByPlaceholder('What needs to be done?').press('Enter');

  await page.locator('.todo-list li').filter({ hasText: 'Completed Item' }).locator('.toggle').check();

  await page.getByRole('link', { name: 'Active' }).click();
  await expect(page.locator('.todo-list li')).toHaveCount(1);
  await expect(page.locator('.todo-list li')).toHaveText('Active Item');

  await page.getByRole('link', { name: 'Completed' }).click();
  await expect(page.locator('.todo-list li')).toHaveCount(1);
  await expect(page.locator('.todo-list li')).toHaveText('Completed Item');
});

test('should toggle all items and clear completed', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Verify Toggle All functionality and Clear Completed button.' },
    { type: 'expected', description: 'All items update status simultaneously; completed items are removed on button click.' }
  );

  await page.getByPlaceholder('What needs to be done?').fill('Item 1');
  await page.getByPlaceholder('What needs to be done?').press('Enter');
  await page.getByPlaceholder('What needs to be done?').fill('Item 2');
  await page.getByPlaceholder('What needs to be done?').press('Enter');

  await page.getByLabel('Mark all as complete').click();
  await expect(page.locator('.todo-list li.completed')).toHaveCount(2);

  const clearBtn = page.getByRole('button', { name: 'Clear completed' });
  await expect(clearBtn).toBeVisible();
  await clearBtn.click();

  await expect(page.locator('.todo-list li')).toHaveCount(0);
  await expect(page.locator('.footer')).toBeHidden();
});
