import Foundation

struct Neighborhood: Codable, Identifiable, Hashable {
    var id: String { slug }
    let slug: String
    let name: String
    let borough: String
}

struct Avenue: Codable, Identifiable {
    var id: String { name }
    let name: String
    let longitude: Double
}
