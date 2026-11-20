//! Native dependency manifest/lockfile and HTTP endpoint AST/regex parsers.

pub mod deps;
pub mod endpoints;

pub use deps::{parse_dependency_file, SbomComponent};
pub use endpoints::{parse_endpoint_file, DiscoveredEndpoint};
