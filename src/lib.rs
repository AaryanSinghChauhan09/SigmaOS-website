#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WebPage {
    pub route: String,
    pub html_template: String,
}

#[derive(Debug, Default)]
pub struct WebsiteCompiler {
    pages: Vec<WebPage>,
}

impl WebsiteCompiler {
    pub fn new() -> Self { Self { pages: Vec::new() } }
    pub fn add_page(&mut self, route: &str, template: &str) {
        self.pages.push(WebPage { route: route.into(), html_template: template.into() });
    }
    pub fn page_count(&self) -> usize { self.pages.len() }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_website_compiler_aaa() {
        // Arrange
        let mut site = WebsiteCompiler::new();
        // Act
        site.add_page("/", "index.html");
        site.add_page("/download", "download.html");
        // Assert
        assert_eq!(site.page_count(), 2);
    }
}
