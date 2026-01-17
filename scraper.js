// scraper.js
const { chromium } = require('playwright');
const fs = require('fs');

class APIDocsScraper {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
    this.endpoints = [];
  }

  async scrape() {
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    
    try {
      console.log(`Navigating to ${this.baseUrl}...`);
      await page.goto(this.baseUrl, { waitUntil: 'networkidle' });
      
      // Wait for content to load
      await page.waitForTimeout(2000);
      
      // Extract endpoint information
      // Adjust these selectors based on your specific API docs structure
      this.endpoints = await page.evaluate(() => {
        const endpoints = [];
        
        // Example selectors - you'll need to customize these
        const endpointElements = document.querySelectorAll('.endpoint, .api-endpoint, [class*="endpoint"]');
        
        endpointElements.forEach(element => {
          const endpoint = {};
          
          // Extract method (GET, POST, etc.)
          const methodEl = element.querySelector('.method, .http-method, [class*="method"]');
          endpoint.method = methodEl ? methodEl.textContent.trim().toUpperCase() : 'GET';
          
          // Extract path
          const pathEl = element.querySelector('.path, .endpoint-path, [class*="path"]');
          endpoint.path = pathEl ? pathEl.textContent.trim() : '';
          
          // Extract description
          const descEl = element.querySelector('.description, .summary, p');
          endpoint.description = descEl ? descEl.textContent.trim() : '';
          
          // Extract parameters
          const paramElements = element.querySelectorAll('.parameter, .param, [class*="parameter"]');
          endpoint.parameters = Array.from(paramElements).map(param => {
            return {
              name: param.querySelector('.param-name, .name')?.textContent.trim() || '',
              type: param.querySelector('.param-type, .type')?.textContent.trim() || 'string',
              required: param.textContent.includes('required'),
              description: param.querySelector('.param-description, .description')?.textContent.trim() || ''
            };
          });
          
          // Extract response information
          const responseEl = element.querySelector('.response, .response-body, [class*="response"]');
          if (responseEl) {
            endpoint.response = responseEl.textContent.trim();
          }
          
          if (endpoint.path) {
            endpoints.push(endpoint);
          }
        });
        
        return endpoints;
      });
      
      console.log(`Scraped ${this.endpoints.length} endpoints`);
      
    } catch (error) {
      console.error('Error scraping:', error);
    } finally {
      await browser.close();
    }
    
    return this.endpoints;
  }

  convertToOpenAPI() {
    const openapi = {
      openapi: '3.0.0',
      info: {
        title: 'Scraped API Documentation',
        version: '1.0.0',
        description: 'API documentation scraped and converted to OpenAPI 3.0'
      },
      servers: [
        {
          url: this.baseUrl,
          description: 'API Server'
        }
      ],
      paths: {}
    };

    this.endpoints.forEach(endpoint => {
      if (!endpoint.path) return;
      
      // Initialize path if it doesn't exist
      if (!openapi.paths[endpoint.path]) {
        openapi.paths[endpoint.path] = {};
      }
      
      // Build operation object
      const operation = {
        summary: endpoint.description || `${endpoint.method} ${endpoint.path}`,
        description: endpoint.description || '',
        parameters: [],
        responses: {
          '200': {
            description: 'Successful response',
            content: {
              'application/json': {
                schema: {
                  type: 'object'
                }
              }
            }
          }
        }
      };
      
      // Add parameters
      if (endpoint.parameters && endpoint.parameters.length > 0) {
        endpoint.parameters.forEach(param => {
          operation.parameters.push({
            name: param.name,
            in: param.in || 'query', // default to query parameter
            required: param.required || false,
            description: param.description || '',
            schema: {
              type: param.type || 'string'
            }
          });
        });
      }
      
      // Add request body for POST/PUT/PATCH
      if (['POST', 'PUT', 'PATCH'].includes(endpoint.method)) {
        operation.requestBody = {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                properties: {}
              }
            }
          }
        };
      }
      
      openapi.paths[endpoint.path][endpoint.method.toLowerCase()] = operation;
    });

    return openapi;
  }

  saveToFile(filename = 'openapi.json') {
    const openapi = this.convertToOpenAPI();
    fs.writeFileSync(filename, JSON.stringify(openapi, null, 2));
    console.log(`OpenAPI spec saved to ${filename}`);
    return openapi;
  }
}

// Usage
async function main() {
  const scraper = new APIDocsScraper('https://api.example.com/docs'); // Replace with your API docs URL
  
  await scraper.scrape();
  scraper.saveToFile('openapi.json');
}

main();