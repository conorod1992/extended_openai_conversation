# Scrape Function Tools

A `scrape` Function Tool fetches a web page and extracts selected content with CSS selectors. Prefer a stable API when one is available; page structure can change. Use public URLs unless you have reviewed how credentials and private responses are handled.

The examples below are retained patterns from the previous reference and validated against the current Function Tool schema. Adapt selectors and output shaping to the target page.

## Example 1

```yaml
function:
  type: scrape
  resource: https://example.com
  value_template: "{{ value }}"
  sensor:
    - name: sensor_name
      select: ".css-selector"
      value_template: "{{ value }}"
```

## Example 2

```yaml
function:
      type: scrape
      resource: https://example.com
      headers:
        User-Agent: "Mozilla/5.0"
        Cookie: "session=abc123"
      sensor:
        - name: data
          select: ".content"
```

## Example 3

```yaml
- spec:
    name: get_ha_version
    description: Use this function to get Home Assistant version
    parameters:
      type: object
      properties:
        dummy:
          type: string
          description: Not used (placeholder)
  function:
    type: scrape
    resource: https://www.home-assistant.io
    value_template: "version: {{version}}, release_date: {{release_date}}"
    sensor:
      - name: version
        select: ".current-version h1"
        value_template: '{{ value.split(":")[1] }}'
      - name: release_date
        select: ".release-date"
        value_template: '{{ value.lower() }}'
```

## Example 4

```yaml
- spec:
    name: get_product_price
    description: Get current price of a product
    parameters:
      type: object
      properties:
        url:
          type: string
          description: Product page URL
      required:
      - url
  function:
    type: scrape
    resource_template: "{{ url }}"
    value_template: "Price: {{ price }}, In Stock: {{ stock }}"
    sensor:
      - name: price
        select: ".price-current"
        value_template: '{{ value | replace("$", "") | float }}'
      - name: stock
        select: ".stock-status"
        value_template: '{{ "yes" if "in stock" in value.lower() else "no" }}'
```

## Example 5

```yaml
- spec:
    name: get_news_headlines
    description: Get latest news headlines
    parameters:
      type: object
      properties:
        dummy:
          type: string
  function:
    type: scrape
    resource: https://news.ycombinator.com
    value_template: >-
      Top Stories:
      1. {{ first_headline }}
      2. {{ second_headline }}
    sensor:
      - name: first_headline
        select: ".titleline > a"
        index: 0
      - name: second_headline
        select: ".titleline > a"
        index: 1
```

